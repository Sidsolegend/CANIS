"""
control/reward.py — reward-on-voluntary-approach, spec §4
==========================================================
The CBF certificate (control/cbf.py) guarantees the robot's own commands never
reduce d_rd at/below the margin, so a d_rd decrease while the robot's OWN
commanded v <= 0 is, by construction, attributable ONLY to the dog choosing
to approach — exactly the event counter-conditioning pays for.

Detection rule: across a rolling REWARD_WINDOW_S window in which EVERY
commanded v was <= REWARD_V_EPS (holding/retreating the whole time), d_rd
dropped by at least REWARD_DROP_M. Then, subject to:
  * REWARD_COOLDOWN_MS  (mirror of CANIS_REWARD_COOLDOWN_MS, 20 s), and
  * MAX_SESSION_REWARDS (mirror of the firmware session cap, 30),
an event is emitted via the callback.

DELIVERY IS NOT ACTUATED HERE. The treat/marker path stays firmware-owned
(ESP32 -> Uno DISPENSE, with ACK/JAM and the firmware's own cooldown + session
cap as the final authority — this module's mirrors only PRE-gate so the Pi
never even asks for an off-policy treat). The contract currently has no
Pi-initiated dispense verb; the callback is the integration point for whatever
the wiring pass adds to INTERFACE_CONTRACT.md (contract change goes through
that file FIRST). Until then the events are logged/counted — which is also
exactly what observation-only sessions need.

This module runs in BOTH modes: with CANIS_MOBILE_ENABLED=0 the commanded v is
identically 0, so every approach is voluntary and the stationary reward
behavior is intact.
"""

from collections import deque

try:
    from . import config as C
except ImportError:
    import config as C


class ApproachReward(object):
    def __init__(self, on_reward=None, cooldown_s=None,
                 window_s=None, drop_m=None, session_cap=None):
        self.on_reward = on_reward
        self.cooldown_s = (C.REWARD_COOLDOWN_MS / 1000.0
                           if cooldown_s is None else float(cooldown_s))
        self.window_s = C.REWARD_WINDOW_S if window_s is None else float(window_s)
        self.drop_m = C.REWARD_DROP_M if drop_m is None else float(drop_m)
        self.session_cap = (C.MAX_SESSION_REWARDS
                            if session_cap is None else int(session_cap))
        self.count = 0
        self.last_at = None
        self._hist = deque()        # (t, d, v_cmd) samples inside the window

    def update(self, now, *, tracked, d, v_cmd):
        """One controller tick. Returns an event dict when a voluntary
        approach is rewarded, else None."""
        if not tracked or d is None:
            self._hist.clear()      # broken track: never bridge a gap
            return None

        self._hist.append((float(now), float(d), float(v_cmd)))
        while self._hist and now - self._hist[0][0] > self.window_s:
            self._hist.popleft()

        t0, d0, _ = self._hist[0]
        if now - t0 < self.window_s * 0.8:
            return None             # window not yet filled: not enough evidence
        if any(v > C.REWARD_V_EPS for _, _, v in self._hist):
            return None             # the robot moved forward: not voluntary
        if d0 - d < self.drop_m:
            return None             # distance did not close enough
        if self.last_at is not None and now - self.last_at < self.cooldown_s:
            return None             # inside the treat cooldown (mirror)
        if self.count >= self.session_cap:
            return None             # session cap (mirror of firmware hard cap)

        self.count += 1
        self.last_at = now
        self._hist.clear()          # a new approach must be earned from scratch
        event = {"event": "voluntary_approach", "at": float(now),
                 "d_m": round(float(d), 3), "drop_m": round(float(d0 - d), 3),
                 "count": self.count}
        if self.on_reward is not None:
            try:
                self.on_reward(event)
            except Exception:
                pass                # a bad callback must never break the loop
        return event
