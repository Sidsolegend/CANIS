"""
exposure_arm.py — two-step (arm -> confirm) exposure-level gate for the dashboard
=================================================================================
EXPOSURE_LEVEL is the single most welfare-critical knob (0 = buzzer only,
1 = orient, 2 = orient + approach). ENGINEERING_REVIEW §1.7 flagged that a lone
accidental action must never flip it — on the firmware a stray double-press of
'm' cycled 0->2 (approach) with no confirmation. This module is the dashboard's
mirror of the firmware's 'm'(arm)/'y'(confirm) defense [C4]:

  arm(level)      -> PROPOSES a target level (a "session arm"); nothing commits.
  confirm(level)  -> COMMITS it, but only if it matches the armed target AND
                     arrives within CONFIRM_S; a stale arm auto-expires so it can
                     never be committed later by an unrelated click.
  disarm()        -> cancels a pending arm.

It is deliberately Flask-free (like event_log.py) so it unit-tests off-server
(optimized/tests/test_exposure.py). The committed value is the operator-
AUTHORIZED level, recorded and shown on the dashboard; the LIVE level shown as
firmware truth still comes from the ESP32's own report — the two are displayed
side by side so an operator can see when they disagree.
"""

import threading


class ExposureArm:
    """Thread-safe arm/confirm state machine for exposure-level changes."""

    LEVEL_MIN = 0
    LEVEL_MAX = 2

    def __init__(self, confirm_s=5.0, authorized_level=0):
        self.confirm_s = float(confirm_s)
        self.authorized_level = int(authorized_level)  # last operator-confirmed level
        self._armed_level = None                        # pending proposal, or None
        self._armed_at = 0.0
        self._lock = threading.Lock()

    @classmethod
    def _valid(cls, level):
        return isinstance(level, int) and cls.LEVEL_MIN <= level <= cls.LEVEL_MAX

    def arm(self, level, now):
        """Propose a target level. Returns (ok, message)."""
        if not self._valid(level):
            return False, f"level must be {self.LEVEL_MIN}..{self.LEVEL_MAX}"
        with self._lock:
            self._armed_level = level
            self._armed_at = now
            return True, f"armed level {level} — confirm within {self.confirm_s:g}s"

    def confirm(self, level, now):
        """Commit the armed level. Returns (ok, message). Fails (and clears the
        arm) if nothing is armed, the level does not match, or the arm expired —
        so a confirm can only ever apply the exact level just armed."""
        with self._lock:
            if self._armed_level is None:
                return False, "nothing armed (arm a level first)"
            if (now - self._armed_at) > self.confirm_s:
                self._armed_level = None
                return False, "arm expired — re-arm and confirm faster"
            if level != self._armed_level:
                # A mismatch is treated as a failed/second action, not a silent
                # commit: clear the arm so the operator must start over.
                pending = self._armed_level
                self._armed_level = None
                return False, f"confirm {level} != armed {pending} — arm cleared"
            self.authorized_level = self._armed_level
            self._armed_level = None
            return True, f"exposure authorized -> {self.authorized_level}"

    def disarm(self, *_):
        """Cancel any pending proposal."""
        with self._lock:
            self._armed_level = None
            return True, "disarmed"

    def status(self, now):
        """Snapshot for the dashboard UI (also expires a stale arm on read)."""
        with self._lock:
            armed = self._armed_level is not None
            if armed and (now - self._armed_at) > self.confirm_s:
                self._armed_level = None      # lazy expiry so the UI never shows a dead arm
                armed = False
            return {
                "armed": armed,
                "armed_level": self._armed_level,
                "seconds_left": round(max(0.0, self.confirm_s - (now - self._armed_at)), 1)
                                if armed else 0.0,
                "authorized_level": self.authorized_level,
                "confirm_window_s": self.confirm_s,
            }
