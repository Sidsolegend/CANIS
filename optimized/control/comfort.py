"""
control/comfort.py — adaptive comfort margin D_comfort(s), spec §5
===================================================================
Estimates a latent stress state s in [0, 1] recursively from the observation
signals the spec names — approach/retreat dynamics (sign of ddot_rd), dwell
time near the robot, and the firmware's arousal estimate — and maps it onto

    D_comfort(s) = D_MIN + s * (D_MAX - D_MIN)        (bounded by config)

Welfare-first asymmetries (all guarded in control/config.py):
  * s starts at 1.0 — maximum standoff. Proximity is EARNED from the dog's own
    behavior, never assumed (spec §5).
  * The margin GROWS fast (flinch/retreat, arousal) and SHRINKS slowly
    (voluntary approach, dwell): S_FLINCH_RATE >= S_APPROACH/DWELL rates.
  * Arousal only ever pushes s UP; calming down does not by itself erase the
    margin — only the dog's approach/dwell behavior earns it back down.
  * The dwell discount requires DOG-EARNED proximity: it applies only after a
    voluntary approach, and the credit is cleared the moment the ROBOT
    commands forward motion (v_cmd > ROBOT_ADVANCE_EPS). A robot that creeps
    to the boundary and lurks cannot "earn" a smaller margin from its own
    initiative.
  * No evidence (dog untracked / range unknown) => s HOLDS. Missing data never
    relaxes the margin.
"""

try:
    from . import config as C
except ImportError:
    import config as C


class ComfortMargin(object):
    def __init__(self, d_min_m=None, d_max_m=None):
        self.d_min = C.D_MIN_M if d_min_m is None else float(d_min_m)
        self.d_max = C.D_MAX_M if d_max_m is None else float(d_max_m)
        if not (0.0 < self.d_min < self.d_max):
            raise ValueError("comfort band must satisfy 0 < d_min < d_max")
        self.s = C.S_INIT
        self._dog_earned = False    # proximity credit: set by voluntary approach

    def set_band(self, d_min_m, d_max_m):
        """Adopt a firmware-announced band (contract §3.1 /config keys
        d_min_cm/d_max_cm). Rejected (held) if inverted/absurd."""
        if 0.0 < d_min_m < d_max_m:
            self.d_min, self.d_max = float(d_min_m), float(d_max_m)

    def d_comfort(self):
        return self.d_min + self.s * (self.d_max - self.d_min)

    def update(self, dt, *, tracked, d, ddot, v_cmd, arousal_norm=None):
        """One controller tick. Returns the (possibly updated) D_comfort in m.

        tracked      — estimator says the dog is currently tracked with range
        d, ddot      — d_rd estimate and its low-passed rate (m, m/s) or None
        v_cmd        — the robot's OWN last commanded linear velocity (m/s)
        arousal_norm — firmware arousal / arousal_max in [0, 1], or None
        """
        dt = max(0.0, min(float(dt), C.TICK_DT_MAX_S))

        # Arousal coupling: upward only (stress raises the margin quickly;
        # calm alone never lowers it). First-order pull toward the arousal
        # level with rate S_AROUSAL_PULL_PER_S (a ~2 s time constant).
        if arousal_norm is not None:
            a = max(0.0, min(1.0, float(arousal_norm)))
            if a > self.s:
                self.s += C.S_AROUSAL_PULL_PER_S * dt * (a - self.s)

        if not tracked or d is None or ddot is None:
            self._dog_earned = False if not tracked else self._dog_earned
            return self.d_comfort()   # no evidence: hold

        if v_cmd > C.ROBOT_ADVANCE_EPS:
            self._dog_earned = False  # the robot took the initiative

        if ddot > C.FLINCH_DDOT_MPS:
            # Flinch/fast retreat — regardless of what the robot was doing
            # (if the robot was advancing, this is exactly the signal that it
            # went too far): margin grows FAST.
            self.s += C.S_FLINCH_RATE * dt
        elif ddot < -C.APPROACH_DDOT_MPS and v_cmd <= C.ROBOT_ADVANCE_EPS:
            # Voluntary approach (robot holding/retreating, distance closing
            # by the DOG's choice): margin shrinks SLOWLY, credit earned.
            self.s -= C.S_APPROACH_RATE * dt
            self._dog_earned = True
        elif (abs(ddot) < C.DWELL_DDOT_MPS
              and d < self.d_comfort() + C.DWELL_BAND_M
              and self._dog_earned
              and v_cmd <= C.ROBOT_ADVANCE_EPS):
            # Lingering near the robot after HIS OWN approach: slow discount.
            self.s -= C.S_DWELL_RATE * dt

        self.s = max(0.0, min(1.0, self.s))
        return self.d_comfort()
