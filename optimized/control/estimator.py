"""
control/estimator.py — state estimator for the CBF safe-motion layer
=====================================================================
Implements docs/SAFE_MOTION_CBF_SPEC.md §2: fuse wheel odometry + IMU heading
+ ToF range + the vision detection (distance/bearing) into the spec's state
vector

    x = [ p_x, p_y, theta, v, omega, d_rd, beta ]
          `- robot pose -'  `-vel-'  `- dog -'

Architecture (spec §9.3: "ship the simpler one first"): a COMPLEMENTARY filter
for the pose channel (encoder dead-reckoning corrected by the IMU yaw, which
is exactly the drift encoders accumulate fastest) plus a SCALAR EKF on the dog
channel d_rd (constant-position dog model with process noise for his motion;
prediction carries the ROBOT's known contribution -v*cos(beta)*dt, so the
residual innovation is the dog's own movement). This is the "complementary
filter + median-gated ToF is a defensible v1" option the spec §2 endorses,
with the d_rd channel done properly as an EKF because that is the number the
barrier certificate trusts.

INPUTS (all wire shapes are LAW, docs/INTERFACE_CONTRACT.md):
  on_telemetry(frame, now)  — §1.2 frames at 10 Hz: ts_ms (uint32 millis,
      wrap-safe here), enc_l/enc_r (cumulative signed counts), odom_v,
      odom_omega, tof_m (-1.0 = no return), heading_deg ([0,360), CLOCKWISE-
      positive from boot per §1.1 — converted to the CCW-positive radians used
      internally).
  on_detection(meta, now)   — §3 optional fields as the dashboard parsed them:
      bearing_deg (0 = ahead, negative = LEFT), distance_m (None = unknown),
      confidence. A zone-only legacy body still refreshes "dog seen" but
      establishes NO range and NO bearing — the barrier then fail-safes.

GATING (spec §2, §9.1): the ToF cannot tell the dog from a wall, so a ToF
return is fused into d_rd ONLY when the vision bearing is fresh AND the dog is
within TOF_GATE_DEG of the optical axis — vision selects whether the ToF
reading is "the dog" or background. An ungated return is ignored, never fused.

FAIL-SAFE POSTURE: everything here degrades toward "unknown", and the
controller maps unknown to "at the boundary" (no distance-reducing motion).
This module never fabricates a range.
"""

import math

try:
    from . import config as C
except ImportError:
    import config as C


def _wrap_pi(a):
    """Wrap an angle to (-pi, pi]."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _num(frame, key, abs_max=None):
    """Defensive field read: float or None (dashboard passes bounded values or
    None; a direct feed could pass junk)."""
    v = frame.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    if abs_max is not None and abs(f) > abs_max:
        return None
    return f


class Estimate(object):
    """Immutable-ish snapshot of the spec §2 state vector + validity flags."""
    __slots__ = ("px", "py", "theta", "v", "omega",
                 "d_rd", "d_sigma", "beta_deg", "ddot",
                 "telem_fresh", "dog_seen", "distance_known", "at")

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


class Estimator(object):
    def __init__(self):
        # Pose channel (complementary: encoders + IMU yaw).
        self.px = 0.0
        self.py = 0.0
        self.theta = 0.0            # CCW-positive radians, boot frame
        self.v = 0.0                # body linear velocity, m/s
        self.omega = 0.0            # body angular velocity, rad/s (CCW+)
        self._enc_l = None          # last cumulative counts (baseline)
        self._enc_r = None
        self._ts_ms = None          # last ESP32 millis() (uint32)
        self._imu_off = None        # boot-frame offset: theta = imu_ccw + off
        self._telem_at = None       # receipt wall-time of the last frame

        # Dog channel (scalar EKF on d_rd + complementary bearing).
        self.d = None               # d_rd estimate, m; None = never established
        self.P = C.D_VAR_MAX        # variance on d
        self.beta_deg = 0.0         # contract sign: negative = LEFT
        self._beta_at = None        # receipt time of the last vision BEARING
        self._det_at = None         # receipt time of the last detection (any body)
        self.ddot = 0.0             # low-passed d_rd rate (dog + robot motion)
        self._d_prev = None
        # Telemetry statistics for the odometry-integrity validation (spec §8).
        self.frames = 0

    # ------------------------------------------------------------------ inputs
    def on_telemetry(self, frame, now):
        """Fuse one contract §1.2 frame. `now` = receipt wall-time (seconds)."""
        ts = _num(frame, "ts_ms")
        if ts is None:
            return
        ts = int(ts) % C.TS_MS_WRAP
        if self._ts_ms is None:
            # First frame: baselines only, nothing to integrate yet.
            self._ts_ms = ts
            self._enc_l = _num(frame, "enc_l")
            self._enc_r = _num(frame, "enc_r")
            self._telem_at = now
            self.frames += 1
            return
        dt_ms = (ts - self._ts_ms) % C.TS_MS_WRAP   # uint32 wrap-safe (§1.2)
        self._ts_ms = ts
        dt = dt_ms / 1000.0
        if dt <= 0.0:
            return
        if dt > C.TELEM_DT_MAX_S:
            dt = C.TELEM_DT_MAX_S   # stall clamp (same idea as CANIS_DT_MAX_S)

        # --- velocities: prefer the firmware's own odometry numbers ---------
        v = _num(frame, "odom_v", abs_max=C.ODOM_V_SANE_MPS)
        w = _num(frame, "odom_omega", abs_max=C.ODOM_W_SANE_RPS)

        # --- pose from encoder deltas (falls back to v/omega integration) ---
        el, er = _num(frame, "enc_l"), _num(frame, "enc_r")
        ds = dth = None
        if el is not None and er is not None and \
                self._enc_l is not None and self._enc_r is not None:
            dl = (el - self._enc_l) / C.ENC_COUNTS_PER_REV * C.WHEEL_CIRCUMFERENCE_M
            dr = (er - self._enc_r) / C.ENC_COUNTS_PER_REV * C.WHEEL_CIRCUMFERENCE_M
            ds = (dl + dr) / 2.0
            dth = (dr - dl) / C.WHEEL_BASE_M
            if v is None:
                v = ds / dt
            if w is None:
                w = dth / dt
        if el is not None:
            self._enc_l = el
        if er is not None:
            self._enc_r = er
        if v is None:
            v = self.v      # hold last (bounded staleness cuts this off anyway)
        if w is None:
            w = self.omega
        if ds is None:
            ds, dth = v * dt, w * dt

        self.theta = _wrap_pi(self.theta + dth)
        self.px += ds * math.cos(self.theta)
        self.py += ds * math.sin(self.theta)
        self.v, self.omega = v, w

        # --- IMU yaw correction (complementary) -----------------------------
        # Contract §1.1: heading_deg is [0, 360) CLOCKWISE-positive => the
        # CCW-positive internal angle is its negation.
        hd = _num(frame, "heading_deg")
        if hd is not None and 0.0 <= hd < 360.0:
            imu_ccw = -math.radians(hd)
            if self._imu_off is None:
                self._imu_off = _wrap_pi(self.theta - imu_ccw)
            target = _wrap_pi(imu_ccw + self._imu_off)
            self.theta = _wrap_pi(
                self.theta + C.HEADING_BLEND * _wrap_pi(target - self.theta))

        # --- dog channel: predict, then (gated) ToF update -------------------
        if self.dog_seen(now) and self.d is not None:
            beta_r = math.radians(self.beta_deg)
            # Bearing propagation under OUR motion (contract sign, right-
            # positive => beta_dot = omega + v*sin(beta)/d, derived from the
            # relative-position kinematics; CCW turn moves the dog rightward
            # in the robot frame).
            d_safe = max(self.d, C.D_MIN_RANGE_M)
            beta_r = _wrap_pi(beta_r + (w + v * math.sin(beta_r) / d_safe) * dt)
            self.beta_deg = math.degrees(beta_r)
            # EKF predict: robot's known contribution; the dog's own motion is
            # the process noise (constant-position dog model, spec §2).
            self.d = max(0.0, self.d - v * math.cos(beta_r) * dt)
            self.P = min(C.D_VAR_MAX, self.P + C.DOG_Q_VAR * dt)

            # ToF update — ONLY when vision certifies the ToF is on the dog.
            tof = _num(frame, "tof_m")
            if (tof is not None and 0.0 <= tof <= C.TOF_MAX_M
                    and self._beta_fresh(now)
                    and abs(self.beta_deg) <= C.TOF_GATE_DEG):
                K = self.P / (self.P + C.TOF_R_VAR)
                self.d += K * (tof - self.d)
                self.P *= (1.0 - K)

            self._update_ddot(dt)

        self._telem_at = now
        self.frames += 1

    def on_detection(self, meta, now):
        """Fuse one contract §3 detection. meta: dashboard-parsed optional
        fields (bearing_deg / distance_m / confidence, any of them None)."""
        bearing = _num(meta, "bearing_deg", abs_max=180.0)
        dist = _num(meta, "distance_m")
        conf = _num(meta, "confidence")
        conf = max(C.VIS_CONF_MIN, min(1.0, conf if conf is not None else 0.6))

        if bearing is not None:
            if self._beta_at is None or not self.dog_seen(now):
                self.beta_deg = bearing              # (re)acquire
            else:
                err = _wrap_pi(math.radians(bearing - self.beta_deg))
                self.beta_deg += C.BETA_VIS_GAIN * math.degrees(err)
            self._beta_at = now

        if dist is not None and dist >= 0.0:
            R = C.VIS_R_VAR_BASE / conf
            if self.d is None or not self.dog_seen(now):
                self.d, self.P = float(dist), R      # (re)acquire
                self._d_prev = None
            else:
                K = self.P / (self.P + R)
                self.d += K * (dist - self.d)
                self.P *= (1.0 - K)

        self._det_at = now   # even a zone-only body refreshes "dog seen"

    # --------------------------------------------------------------- internals
    def _update_ddot(self, dt):
        if self._d_prev is not None and dt > 0.0:
            raw = (self.d - self._d_prev) / dt
            a = dt / (C.DDOT_TAU_S + dt)
            self.ddot += a * (raw - self.ddot)
        self._d_prev = self.d

    def _beta_fresh(self, now):
        return self._beta_at is not None and (now - self._beta_at) <= C.DET_HOLD_S

    # ------------------------------------------------------------------ output
    def dog_seen(self, now):
        """A detection (any contract §3 body) landed within DET_HOLD_S."""
        return self._det_at is not None and (now - self._det_at) <= C.DET_HOLD_S

    def telem_fresh(self, now):
        return self._telem_at is not None and (now - self._telem_at) <= C.TELEM_STALE_S

    def snapshot(self, now):
        e = Estimate()
        e.px, e.py, e.theta = self.px, self.py, self.theta
        e.v, e.omega = self.v, self.omega
        e.telem_fresh = self.telem_fresh(now)
        e.dog_seen = self.dog_seen(now)
        # d_rd is usable by the barrier only while the dog is actively tracked
        # AND a range was actually established (a zone-only reporter, or a
        # detection stream with distance_m: null, never fabricates one).
        e.distance_known = bool(e.dog_seen and self.d is not None
                                and self._beta_fresh(now))
        e.d_rd = self.d if e.distance_known else None
        e.d_sigma = math.sqrt(self.P) if e.distance_known else None
        e.beta_deg = self.beta_deg if e.distance_known else None
        e.ddot = self.ddot if e.distance_known else None
        e.at = now
        return e
