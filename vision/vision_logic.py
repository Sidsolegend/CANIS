"""
vision_logic.py - pure decision logic for the CANIS vision pipeline
===================================================================
Everything here is hardware-free and cv2-free ON PURPOSE: the camera and the
ArUco detector live in detect.py, but the two policies below are plain state
machines, so they are extracted here and unit-tested off the Pi
(tests/test_vision_logic.py). detect.py imports this module.

  DetectionGrace   [OPT-12] marker-loss grace period. ArUco loses the collar
                   marker for a few frames whenever the dog turns his head or
                   the marker tilts past ~60 deg. Without a grace period the
                   posted detection flaps, the dashboard's COMMAND_TTL expires
                   and re-arms, and the ESP32's arousal saw-tooths - which can
                   fire a reward for "leaving" at a dog that merely looked
                   away. The grace period bridges short occlusions by holding
                   the last zone for GRACE_S after the last real sighting.
                   Welfare trade-off (documented, deliberate): the real
                   "dog left" clear is delayed by up to GRACE_S, which delays
                   the leaving-reward by the same amount. Keep GRACE_S small
                   (default 1.5 s) relative to the arousal decay time (~5 s).

  FrozenFrameGuard [OPT-13] stale-frame guard. A wedged camera driver (seen
                   with UVC webcams and, rarely, picamera2) can keep returning
                   the SAME buffer forever: reads "succeed", so the [OPT-6b]
                   read-failure reconnect never fires, and the system watches
                   a frozen photo of an empty room while believing it has live
                   vision. This guard fingerprints a cheap subsample of each
                   frame; too many identical fingerprints in a row means the
                   stream is frozen and the camera must be reopened.

  BlurSkipMonitor  [C6] blur-guard skip-ratio telemetry. detect.is_sharp()
                   rejects low-Laplacian-variance frames - but variance drops
                   with LOW LIGHT / low contrast, not just motion blur. At dusk the guard can silently
                   reject ~100% of frames, so vision contributes nothing and the
                   system quietly degrades to PIR + ultrasonic (safe, but
                   UNMEASURED). This monitor tracks the fraction of frames
                   skipped over a rolling window and raises a rate-limited alert
                   when it exceeds a threshold (default > 90% over 60 s), turning
                   a silent blackout into a logged, visible one.
"""

from collections import deque


class DetectionGrace:
    """Hold a detection for grace_s after the marker was last actually seen."""

    def __init__(self, grace_s=1.5):
        self.grace_s = float(grace_s)
        self._zone = None       # last zone actually seen
        self._last_seen = None  # timestamp of last real sighting

    def update(self, seen, zone, now):
        """Feed one processed frame's result.

        seen  -- True if the dog's marker was found in this frame
        zone  -- the zone label for this sighting (ignored when seen is False)
        now   -- current time (seconds)

        Returns the zone that should be reported right now, or None:
          - a real sighting returns (and remembers) its zone;
          - within grace_s of the last sighting, the remembered zone is held;
          - past the grace window, None (detection genuinely cleared).
        """
        if seen:
            self._zone = zone
            self._last_seen = now
            return zone
        if self._last_seen is not None and (now - self._last_seen) <= self.grace_s:
            return self._zone
        self._zone = None
        return None

    @property
    def holding(self):
        """True if the last update returned a held (not directly seen) zone."""
        return self._zone is not None


class FrozenFrameGuard:
    """Detect a wedged camera that keeps returning the identical frame."""

    def __init__(self, max_identical=30):
        # 30 processed frames at ~10 fps processed = ~3 s of a truly frozen
        # stream. Real video always has sensor noise, so identical fingerprints
        # essentially never happen on a live camera.
        self.max_identical = int(max_identical)
        self._last_sig = None
        self._streak = 0

    def update(self, signature):
        """Feed one frame's fingerprint. Returns True when the stream looks frozen."""
        if signature == self._last_sig:
            self._streak += 1
        else:
            self._streak = 0
            self._last_sig = signature
        return self._streak >= self.max_identical

    def reset(self):
        """Call after reopening the camera."""
        self._last_sig = None
        self._streak = 0


class BlurSkipMonitor:
    """Track the blur-guard skip ratio over a rolling window and alert on a
    low-light blackout [C6]."""

    def __init__(self, window_s=60.0, alert_ratio=0.9, min_samples=20):
        # min_samples avoids alerting on a tiny early sample (e.g. the first two
        # frames both blurry -> 100% is not yet meaningful).
        self.window_s = float(window_s)
        self.alert_ratio = float(alert_ratio)
        self.min_samples = int(min_samples)
        self._events = deque()   # (ts, skipped_bool) for frames the blur gate saw
        self._skipped = 0        # running count of skipped events in the window
        self._last_alert = None  # ts of the last alert (rate-limit to 1/window)

    def record(self, skipped, now):
        """Log one blur-gated frame (skipped=True if is_sharp() rejected it).
        Returns an alert dict the first time the window crosses the threshold
        (then at most once per window_s), else None."""
        self._events.append((now, bool(skipped)))
        if skipped:
            self._skipped += 1
        self._prune(now)
        total = len(self._events)
        if total < self.min_samples:
            return None
        ratio = self._skipped / total
        if ratio > self.alert_ratio and (
                self._last_alert is None or (now - self._last_alert) >= self.window_s):
            self._last_alert = now
            return {"ratio": round(ratio, 3), "skipped": self._skipped,
                    "total": total, "window_s": self.window_s}
        return None

    def ratio(self, now):
        """Current skip ratio over the window (0.0 when no samples)."""
        self._prune(now)
        total = len(self._events)
        return (self._skipped / total) if total else 0.0

    def _prune(self, now):
        cutoff = now - self.window_s
        while self._events and self._events[0][0] < cutoff:
            _, was_skipped = self._events.popleft()
            if was_skipped:
                self._skipped -= 1
