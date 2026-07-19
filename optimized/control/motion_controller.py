#!/usr/bin/env python3
"""
CANIS — Pi-side motion controller (CBF safe-motion layer)  *** NEW ***
======================================================================
The host tier of docs/SAFE_MOTION_CBF_SPEC.md: estimator (§2) + adaptive
comfort margin (§5) + closed-form CBF-QP safety filter (§3-§4) +
reward-on-voluntary-approach (§4), composed with every existing fail-safe
(§6). Wire formats are docs/INTERFACE_CONTRACT.md (LAW) — this module only
ever produces the post-QP u* that GET /motion serves as "MOVE <v> <omega>" or
"STOP"; it never invents an endpoint.

PIPELINE (one tick, ideally driven at >= CANIS_ODOM_STREAM_HZ):

    /telemetry ---> Estimator ---(d_rd, beta, ddot, pose)---> ComfortMargin
    /detect  ----->     |                                          |
    /ingest  --(arousal, exposure)--+                        D_comfort(s)
                                    v                              v
                     nominal policy u_nom ---> CBF-QP filter ---> u* ---> /motion
                                                    ^
                                 h = (d_rd - k*sigma) - D_comfort

MODE GATING (contract §4):
  * CANIS_MOBILE_ENABLED = 0 (SAFE DEFAULT, env-mirrored): tick() returns None
    and NO drive command is ever deposited — the sink is never touched. The
    estimator, comfort margin, reward-on-approach, and status() telemetry all
    keep running, so the stationary study gets the estimation + reward layer
    with zero motion risk.
  * = 1: the full CBF loop runs, but every EXISTING fail-safe still outranks
    it (spec §6): exposure gate (level 0 => STOP, level 1 => pivot only),
    telemetry staleness => STOP, unknown distance => treated as AT THE
    BOUNDARY (no distance-reducing command), and downstream the ESP32 clamps +
    re-checks NEVER_APPROACH and the Uno's 1000 ms watchdog stays supreme.

DASHBOARD WIRING (in-process, matches dashboard/app.py's MotionCommand.set
design note — left to the wiring pass, shown here for the record):

    from control.motion_controller import MotionController
    ctl = MotionController(sink=motion)          # dashboard's MotionCommand
    # /telemetry handler:  ctl.ingest_telemetry(frame); ctl.tick()
    # /detect handler:     ctl.ingest_detection(meta)
    # /ingest handler:     ctl.ingest_state(payload)
    # /config handler:     ctl.ingest_config(clean)

Ticking on /telemetry arrival gives exactly the spec's >= 10 Hz; if the stream
dies, nothing ticks, the deposited command expires within MOTION_CMD_TTL_MS,
and /motion serves STOP — the safe default with no extra machinery. An
optional start_ticker() thread exists for driving tick() at CONTROL_RATE_HZ
independently of telemetry (it emits STOP on stale telemetry by design).

SELFTEST (no hardware, no network, stdlib only):

    python3 control/motion_controller.py --selftest        # or: python3 -m control.motion_controller --selftest

Runs the estimator + comfort + reward + CBF stack against SYNTHETIC worlds
(scripted robot/dog kinematics -> contract-shaped telemetry/detections) and
ASSERTS, among ~40 checks, the headline invariant: the barrier NEVER permits a
distance-reducing command at/below the comfort margin — including under
detection dropout, telemetry dropout, saturation, and estimator noise.
"""

import argparse
import math
import re
import threading
import time

try:                        # package import (dashboard side)
    from . import config as C
    from .cbf import filter_command, clamp
    from .comfort import ComfortMargin
    from .estimator import Estimator
    from .reward import ApproachReward
except ImportError:         # direct script run: python3 control/motion_controller.py
    import config as C
    from cbf import filter_command, clamp
    from comfort import ComfortMargin
    from estimator import Estimator
    from reward import ApproachReward


# ---------------- Nominal policy (spec §4: the barrier filters a reference) ---
def nominal_hold_and_face(est):
    """Default u_nom: hold position and pivot to face the dog. Facing is not
    approaching (the constraint has no omega component), so this passes the
    barrier untouched — and a robot that faces the dog keeps him inside the
    ToF gate, which is what keeps d_rd measurable. Roam/flee/behavior policies
    plug in by replacing MotionController.nominal; whatever they propose, the
    barrier filters."""
    if est.distance_known and est.beta_deg is not None:
        # beta positive = dog to the RIGHT; turning clockwise (omega < 0)
        # drives beta -> 0.
        w = clamp(-C.FACE_KP * math.radians(est.beta_deg),
                  -C.FACE_W_MAX, C.FACE_W_MAX)
        return 0.0, w
    return 0.0, 0.0


class MotionController(object):
    """Thread-safe (dashboard handlers run on multiple Flask threads)."""

    def __init__(self, mobile_enabled=None, sink=None, on_reward=None,
                 nominal=None):
        self._lock = threading.RLock()
        self.mobile = bool(C.MOBILE_ENABLED if mobile_enabled is None
                           else mobile_enabled)
        self.sink = sink                    # duck-typed: .set(v, w) / .stop()
        self.nominal = nominal or nominal_hold_and_face
        self.gamma = C.CBF_GAMMA
        self.estimator = Estimator()
        self.comfort = ComfortMargin()
        self.reward = ApproachReward(on_reward=on_reward)
        # Power-on exposure mirrors CANIS_EXPOSURE_DEFAULT (0 = no motion):
        # until the firmware reports otherwise via /ingest, the gate is shut.
        self.exposure_level = C.EXPOSURE_DEFAULT
        self.arousal_norm = None
        self._last_cmd = (0.0, 0.0)
        self._last_line = None
        self._last_cbf = None
        self._last_tick = None
        self._ticker = None
        self._ticker_stop = threading.Event()

    # ------------------------------------------------------------- ingestion
    def ingest_telemetry(self, frame, now=None):
        """Contract §1.2 frame (dict), as parsed/bounded by the dashboard."""
        with self._lock:
            self.estimator.on_telemetry(frame, time.time() if now is None else now)

    def ingest_detection(self, meta, now=None):
        """Contract §3 optional fields (dict); zone-only bodies pass {} here
        (they refresh presence but establish no range — fail-safe applies)."""
        with self._lock:
            self.estimator.on_detection(meta or {}, time.time() if now is None else now)

    def ingest_state(self, payload, now=None):
        """Contract §1.1 /ingest body: adopt exposure_level (the OUTER
        authority, spec §6) and the arousal estimate for the comfort margin."""
        with self._lock:
            try:
                lvl = int(payload.get("exposure_level"))
                self.exposure_level = max(0, min(2, lvl))
            except (TypeError, ValueError):
                pass
            try:
                a = float(payload.get("arousal"))
                am = float(payload.get("arousal_max") or 100.0)
                if am > 0 and a == a:
                    self.arousal_norm = max(0.0, min(1.0, a / am))
            except (TypeError, ValueError):
                pass

    def ingest_config(self, conf):
        """Contract §3.1 firmware /config announce — runtime anti-drift.
        Announced values are adopted only within conservative bounds, and
        mobile can only ever DEGRADE toward off (a firmware that says
        stationary is believed; one that says mobile cannot force the Pi)."""
        with self._lock:
            me = conf.get("mobile_enabled")
            if me is not None and not me:
                self.mobile = False
            g = conf.get("cbf_gamma")
            try:
                g = float(g)
                if 0.0 < g <= C.CBF_GAMMA_MAX:
                    self.gamma = g
            except (TypeError, ValueError):
                pass
            try:
                dmin, dmax = float(conf.get("d_min_cm")), float(conf.get("d_max_cm"))
                self.comfort.set_band(dmin / 100.0, dmax / 100.0)
            except (TypeError, ValueError):
                pass

    # ------------------------------------------------------------------ tick
    def tick(self, now=None):
        """One control step. Returns the exact contract §2.1 line deposited
        ("MOVE <v> <omega>" or "STOP"), or None in stationary mode (NO drive
        command was issued at all)."""
        with self._lock:
            now = time.time() if now is None else now
            est = self.estimator.snapshot(now)
            dt = (0.0 if self._last_tick is None
                  else max(0.0, min(now - self._last_tick, C.TICK_DT_MAX_S)))
            self._last_tick = now
            v_prev = self._last_cmd[0]

            # Estimation / adaptation / reward run in BOTH modes (stationary
            # study keeps the reward-on-approach layer; commanded v is 0).
            self.comfort.update(dt, tracked=est.distance_known, d=est.d_rd,
                                ddot=est.ddot, v_cmd=v_prev,
                                arousal_norm=self.arousal_norm)
            self.reward.update(now, tracked=est.distance_known, d=est.d_rd,
                               v_cmd=v_prev)

            # (5) Mode gate: stationary => NO drive commands, ever.
            if not self.mobile:
                self._last_cmd = (0.0, 0.0)
                self._last_line = None
                self._last_cbf = None
                return None

            # Fail-safe chain, outermost authorities first (spec §6):
            # 1) stale estimate => the barrier cannot be evaluated => STOP.
            if not est.telem_fresh:
                return self._emit(0.0, 0.0, None)
            # 2) exposure gate: level 0 forbids all motion, full stop.
            if self.exposure_level <= 0:
                return self._emit(0.0, 0.0, None)

            v_nom, w_nom = self.nominal(est)
            # 3) exposure level 1 = orient only: no translation is even
            #    proposed (the barrier can then only keep it that way).
            if self.exposure_level < 2:
                v_nom = 0.0

            # 4) The barrier. Unknown distance => distance_known=False =>
            #    treated as AT THE BOUNDARY, dog dead ahead (worst case).
            #    Known distance is used CONSERVATIVELY: the estimator's
            #    1-sigma (capped) is subtracted, so uncertainty always
            #    shrinks h, never inflates it.
            if est.distance_known:
                d_cons = est.d_rd - min(est.d_sigma, C.D_UNCERT_CAP_M)
                h = d_cons - self.comfort.d_comfort()
                v, w, info = filter_command(v_nom, w_nom, h_m=h,
                                            beta_deg=est.beta_deg,
                                            gamma=self.gamma)
            else:
                v, w, info = filter_command(v_nom, w_nom, h_m=0.0,
                                            beta_deg=0.0,
                                            distance_known=False,
                                            gamma=self.gamma)
            return self._emit(v, w, info)

    def _emit(self, v, w, info):
        """Deposit + record one post-QP command; return the contract line."""
        self._last_cmd = (v, w)
        self._last_cbf = info
        if abs(v) < C.MOVE_EPS and abs(w) < C.MOVE_EPS:
            line = "STOP"       # contract §2.1: a hold is served as STOP
            if self.sink is not None:
                if hasattr(self.sink, "stop"):
                    self.sink.stop()
                elif callable(self.sink):
                    self.sink(0.0, 0.0)
        else:
            line = "MOVE %.3f %.3f" % (v, w)
            if self.sink is not None:
                if hasattr(self.sink, "set"):
                    self.sink.set(v, w)
                elif callable(self.sink):
                    self.sink(v, w)
        self._last_line = line
        return line

    # ---------------------------------------------------------------- status
    def status(self, now=None):
        """Display/telemetry snapshot (dashboard /api surface material)."""
        with self._lock:
            now = time.time() if now is None else now
            est = self.estimator.snapshot(now)
            cbf = self._last_cbf or {}
            return {
                "mode": "mobile" if self.mobile else "stationary",
                "exposure_level": self.exposure_level,
                "telem_fresh": est.telem_fresh,
                "dog_seen": est.dog_seen,
                "distance_known": est.distance_known,
                "d_rd_m": None if est.d_rd is None else round(est.d_rd, 3),
                "d_sigma_m": None if est.d_sigma is None else round(est.d_sigma, 3),
                "beta_deg": None if est.beta_deg is None else round(est.beta_deg, 1),
                "ddot_mps": None if est.ddot is None else round(est.ddot, 3),
                "d_comfort_m": round(self.comfort.d_comfort(), 3),
                "stress_s": round(self.comfort.s, 3),
                "h_m": (None if cbf.get("h") is None or not est.distance_known
                        else round(cbf["h"], 3)),
                "cbf_active": bool(cbf.get("active")),
                "cbf_gamma": self.gamma,
                "cmd_v": round(self._last_cmd[0], 3),
                "cmd_omega": round(self._last_cmd[1], 3),
                "motion_line": self._last_line,
                "rewards": self.reward.count,
                "pose": [round(est.px, 3), round(est.py, 3),
                         round(math.degrees(est.theta), 1)],
            }

    # ------------------------------------------------- optional ticker thread
    def start_ticker(self, rate_hz=None):
        """Drive tick() at CONTROL_RATE_HZ independently of telemetry arrival.
        Safe by construction: with no fresh telemetry every tick emits STOP."""
        if self._ticker is not None:
            return
        period = 1.0 / float(rate_hz or C.CONTROL_RATE_HZ)
        self._ticker_stop.clear()

        def loop():
            while not self._ticker_stop.wait(period):
                self.tick()

        self._ticker = threading.Thread(target=loop, daemon=True)
        self._ticker.start()

    def stop_ticker(self):
        self._ticker_stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=1.0)
            self._ticker = None


# ============================================================================
# --selftest: estimator + CBF on SYNTHETIC data (no hardware, no network)
# ============================================================================
_MOVE_RE = re.compile(r"^MOVE -?\d+\.\d{3} -?\d+\.\d{3}$")


class _Rng(object):
    """Tiny deterministic LCG so the selftest never depends on random's
    platform behavior and is byte-reproducible."""

    def __init__(self, seed=0xC4415):
        self.s = seed & 0x7FFFFFFF

    def uniform(self, lo, hi):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return lo + (hi - lo) * (self.s / 0x7FFFFFFF)

    def gauss(self, sigma):
        # Irwin-Hall(12) approximation — plenty for test noise.
        return sigma * (sum(self.uniform(0.0, 1.0) for _ in range(12)) - 6.0)


class _World(object):
    """Scripted unicycle robot + movable dog -> contract-shaped frames.
    ts_ms deliberately starts 1.5 s before the uint32 wrap so every sim also
    exercises the §1.2 millis() wrap handling."""

    def __init__(self, dog_xy=(4.0, 0.0), rng=None):
        self.x = self.y = self.th = 0.0
        self.dog = [float(dog_xy[0]), float(dog_xy[1])]
        self.enc_l = self.enc_r = 0.0
        self.t = 0.0
        self.ts0 = (1 << 32) - 1500
        self.rng = rng

    def step(self, dt, v, w):
        self.th = (self.th + w * dt + math.pi) % (2 * math.pi) - math.pi
        self.x += v * dt * math.cos(self.th)
        self.y += v * dt * math.sin(self.th)
        half = C.WHEEL_BASE_M / 2.0
        for attr, vw in (("enc_l", v - w * half), ("enc_r", v + w * half)):
            setattr(self, attr, getattr(self, attr)
                    + vw * dt / C.WHEEL_CIRCUMFERENCE_M * C.ENC_COUNTS_PER_REV)
        self.t += dt

    def truth(self):
        dx, dy = self.dog[0] - self.x, self.dog[1] - self.y
        d = math.hypot(dx, dy)
        alpha = (math.atan2(dy, dx) - self.th + math.pi) % (2 * math.pi) - math.pi
        return d, -math.degrees(alpha)      # contract sign: negative = LEFT

    def telemetry(self, v, w, tof_sigma=0.0):
        d, beta = self.truth()
        n = (self.rng.gauss(tof_sigma) if (self.rng and tof_sigma) else 0.0)
        tof = (round(d + n, 3) if (abs(beta) <= C.TOF_GATE_DEG and d <= C.TOF_MAX_M)
               else -1.0)                   # -1.0 = the ONLY no-data sentinel
        return {"ts_ms": int(self.ts0 + self.t * 1000.0) % (1 << 32),
                "enc_l": int(self.enc_l), "enc_r": int(self.enc_r),
                "odom_v": round(v, 3), "odom_omega": round(w, 3),
                "tof_m": tof,
                "heading_deg": (-math.degrees(self.th)) % 360.0}

    def detection(self, dist_sigma=0.0, bearing_sigma=0.0):
        d, beta = self.truth()
        dn = self.rng.gauss(dist_sigma) if (self.rng and dist_sigma) else 0.0
        bn = self.rng.gauss(bearing_sigma) if (self.rng and bearing_sigma) else 0.0
        return {"dog_present": True, "bearing_deg": round(beta + bn, 1),
                "distance_m": round(max(0.0, d + dn), 3), "confidence": 0.9}


def _parse_line(line):
    if line is None or line == "STOP":
        return 0.0, 0.0
    parts = line.split()
    return float(parts[1]), float(parts[2])


def _run_sim(ctl, world, duration_s, *, dog_vx=0.0, dog_stop_at_d=None,
             det_until=None, on_tick=None):
    """Closed loop at 20 Hz control / 10 Hz telemetry / 1 Hz detection; the
    controller's own commands drive the world (perfect wheel tracking)."""
    dt, now0 = 0.05, 1000.0
    v_cmd = w_cmd = 0.0
    steps = int(round(duration_s / dt))
    for i in range(steps):
        now = now0 + world.t
        if i % 2 == 0:
            ctl.ingest_telemetry(world.telemetry(v_cmd, w_cmd, tof_sigma=0.01),
                                 now=now)
        if i % 20 == 0 and (det_until is None or world.t < det_until):
            ctl.ingest_detection(world.detection(dist_sigma=0.15,
                                                 bearing_sigma=0.5), now=now)
        line = ctl.tick(now=now)
        v_cmd, w_cmd = _parse_line(line)
        if on_tick is not None:
            on_tick(now, world, line, v_cmd, w_cmd)
        # scripted dog motion (straight along -x toward the robot's start)
        if dog_vx:
            d_true, _ = world.truth()
            if dog_stop_at_d is None or d_true > dog_stop_at_d:
                world.dog[0] += dog_vx * dt
        world.step(dt, v_cmd, w_cmd)


class _RecSink(object):
    def __init__(self):
        self.sets, self.stops = [], 0

    def set(self, v, w):
        self.sets.append((v, w))

    def stop(self):
        self.stops += 1


def run_selftest():
    fails = []

    def check(name, cond, detail=""):
        print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                               " (%s)" % detail if detail else ""))
        if not cond:
            fails.append(name)

    EPS = 1e-9

    # ---------------------------------------------------------------- 1/5
    print("[selftest] 1/5 CBF closed-form QP projection (spec section 3-4)")
    v, w, info = filter_command(0.30, 0.5, h_m=-0.10, beta_deg=0.0)
    check("spoofed h<0, dog ahead: forward clamps to a RETREAT (spec section 6 bench test)",
          v <= -0.049 and w == 0.5, "v=%.3f" % v)
    v, _, _ = filter_command(0.0, 0.0, h_m=-0.10, beta_deg=180.0)
    check("h<0, dog BEHIND: hold is rewritten to drive AWAY (v>0)", v >= 0.049,
          "v=%.3f" % v)
    v, _, info = filter_command(0.10, 0.0, h_m=0.50, beta_deg=0.0)
    check("safe nominal passes untouched (minimal intervention)",
          v == 0.10 and not info["active"])
    v, _, _ = filter_command(0.30, 0.0, h_m=0.0, beta_deg=0.0, distance_known=False)
    check("unknown distance => at-boundary fail-safe => v <= 0", v <= EPS)
    v, _, _ = filter_command(0.30, 0.0, h_m=-0.20, beta_deg=90.0)
    check("dog exactly abeam at h<0: v holds at 0 (no guessed direction)",
          v == 0.0)
    v, w, _ = filter_command(5.0, -9.0, h_m=2.0, beta_deg=0.0)
    check("actuator box respected", abs(v) <= C.MAX_LINEAR_MPS + EPS
          and abs(w) <= C.MAX_ANGULAR_RPS + EPS)
    v, _, info = filter_command(0.30, 0.0, h_m=-10.0, beta_deg=0.0)
    check("demanded retreat beyond v_max saturates at -v_max (still opening)",
          v == -C.MAX_LINEAR_MPS and info["saturated"])

    rng = _Rng()
    bad = 0
    for _ in range(5000):
        h = rng.uniform(-1.0, 2.0)
        beta = rng.uniform(-180.0, 180.0)
        vn, wn = rng.uniform(-0.6, 0.6), rng.uniform(-2.5, 2.5)
        v, w, info = filter_command(vn, wn, h_m=h, beta_deg=beta)
        cosb = math.cos(math.radians(beta))
        if abs(v) > C.MAX_LINEAR_MPS + EPS or abs(w) > C.MAX_ANGULAR_RPS + EPS:
            bad += 1
        elif h <= 0.0 and v * cosb > EPS:
            bad += 1        # THE invariant: no distance-reducing command at/below margin
        elif h > 0.0 and not info["saturated"] and v * cosb > C.CBF_GAMMA * h + 1e-6:
            bad += 1        # constraint satisfied whenever not box-saturated
    check("5000-sample fuzz: barrier invariant + box + constraint hold",
          bad == 0, "%d violations" % bad)

    # ---------------------------------------------------------------- 2/5
    print("[selftest] 2/5 estimator: odometry+IMU fusion, ts_ms wrap, ToF gating")
    est, wld = Estimator(), _World(dog_xy=(50.0, 50.0))   # dog far: pose only
    now = 1000.0
    legs = [(0.2, 0.0, 5.0), (0.0, 0.5, math.pi / 2 / 0.5)] * 4  # 1 m + 90 deg CCW, x4
    for v, w, dur in legs:
        for _ in range(int(round(dur / 0.1))):
            wld.step(0.1, v, w)
            now += 0.1
            est.on_telemetry(wld.telemetry(v, w), now)
    perr = math.hypot(est.px - wld.x, est.py - wld.y)
    terr = abs((est.theta - wld.th + math.pi) % (2 * math.pi) - math.pi)
    check("square drive: pose error < 5 cm across the uint32 millis() wrap",
          perr < 0.05, "%.3f m" % perr)
    check("square drive: heading error < 2 deg", terr < math.radians(2.0),
          "%.2f deg" % math.degrees(terr))

    est2 = Estimator()
    now = 2000.0
    est2.on_detection({"bearing_deg": 90.0, "distance_m": 2.0,
                       "confidence": 0.9}, now)
    w2 = _World(dog_xy=(0.0, -2.0))     # true dog at beta=+90 (right)
    frame = w2.telemetry(0.0, 0.0)
    frame["tof_m"] = 0.5                # a WALL return, dead ahead
    est2.on_telemetry(dict(frame), now)             # baseline frame
    frame2 = dict(frame)
    frame2["ts_ms"] = (frame["ts_ms"] + 100) % (1 << 32)
    est2.on_telemetry(frame2, now + 0.1)
    check("ToF ranging a wall while the dog is abeam is NOT fused (vision gate)",
          est2.d is not None and est2.d > 1.5, "d=%.2f" % est2.d)
    snap = est2.snapshot(now + 0.2)
    check("dog tracked with range => distance_known", snap.distance_known)
    snap = est2.snapshot(now + C.DET_HOLD_S + 1.0)
    check("detection hold expired => distance UNKNOWN (never held forever)",
          not snap.distance_known and snap.d_rd is None)
    est3 = Estimator()
    est3.on_detection({}, 3000.0)       # legacy zone-only body: presence, no range
    snap = est3.snapshot(3000.1)
    check("zone-only detection: dog seen but NO fabricated range",
          snap.dog_seen and not snap.distance_known)

    # ---------------------------------------------------------------- 3/5
    print("[selftest] 3/5 closed loop: aggressive nominal vs. the barrier (dog fixed)")
    ctl = MotionController(mobile_enabled=True, sink=_RecSink())
    ctl.ingest_state({"exposure_level": 2, "arousal": 0.0, "arousal_max": 100.0})
    ctl.nominal = lambda est: (0.30, 0.0)           # relentless approach request
    wld = _World(dog_xy=(4.0, 0.0), rng=_Rng(7))
    rec = {"min_h_true": 9e9, "viol": 0, "lines_bad": 0, "max_x": 0.0,
           "last_v": 0.0}

    def on_tick(now, world, line, v_cmd, w_cmd):
        if line is not None and line != "STOP" and not _MOVE_RE.match(line):
            rec["lines_bad"] += 1
        d_true, beta_true = world.truth()
        h_true = d_true - ctl.comfort.d_comfort()
        rec["min_h_true"] = min(rec["min_h_true"], h_true)
        st = ctl.status(now)
        if st["h_m"] is not None and st["h_m"] <= 0.0:
            if v_cmd * math.cos(math.radians(beta_true)) > EPS:
                rec["viol"] += 1        # distance-reducing command at/below margin
        rec["max_x"] = max(rec["max_x"], world.x)
        rec["last_v"] = v_cmd

    _run_sim(ctl, wld, 30.0, on_tick=on_tick)
    check("every emitted line is contract-exact ('MOVE %.3f %.3f' | 'STOP')",
          rec["lines_bad"] == 0)
    check("robot actually advanced under the nominal (test not vacuous)",
          rec["max_x"] > 1.2, "%.2f m" % rec["max_x"])
    check("ASSERT: zero distance-reducing commands at/below the margin",
          rec["viol"] == 0, "%d" % rec["viol"])
    check("true distance never crossed D_comfort (h_true >= -5 cm incl. noise)",
          rec["min_h_true"] >= -0.05, "min h=%.3f m" % rec["min_h_true"])
    check("robot settled at the boundary (final v ~ 0)", abs(rec["last_v"]) <= 0.02,
          "%.3f" % rec["last_v"])
    check("no reward for a ROBOT-caused distance drop", ctl.reward.count == 0)
    d_true, _ = wld.truth()
    st = ctl.status(1000.0 + wld.t)
    check("estimator range tracks truth (<15 cm at settle)",
          st["d_rd_m"] is not None and abs(st["d_rd_m"] - d_true) < 0.15,
          "est=%s true=%.2f" % (st["d_rd_m"], d_true))

    # ---------------------------------------------------------------- 4/5
    print("[selftest] 4/5 voluntary approach: reward + adaptive comfort margin")
    events = []
    ctl = MotionController(mobile_enabled=True, sink=_RecSink(),
                           on_reward=events.append)
    ctl.ingest_state({"exposure_level": 2, "arousal": 0.0, "arousal_max": 100.0})
    wld = _World(dog_xy=(3.0, 0.0), rng=_Rng(11))
    rec = {"v_pos": 0, "h_neg_seen": False}

    def on_tick(now, world, line, v_cmd, w_cmd):
        if v_cmd > EPS:
            rec["v_pos"] += 1
        st = ctl.status(now)
        if st["h_m"] is not None and st["h_m"] <= 0.0:
            rec["h_neg_seen"] = True

    _run_sim(ctl, wld, 25.0, dog_vx=-0.15, dog_stop_at_d=0.9, on_tick=on_tick)
    check("holding robot NEVER commanded v > 0 while the dog approached",
          rec["v_pos"] == 0, "%d" % rec["v_pos"])
    check("dog crossed the margin by HIS OWN choice (h<0 reached, certificate intact)",
          rec["h_neg_seen"])
    check("voluntary approach REWARDED (v<=0 while d dropped)", len(events) >= 1,
          "%d events" % len(events))
    gaps_ok = all(b["at"] - a["at"] >= C.REWARD_COOLDOWN_MS / 1000.0 - 1e-6
                  for a, b in zip(events, events[1:]))
    check("reward cooldown respected (mirror of CANIS_REWARD_COOLDOWN_MS)", gaps_ok)
    check("comfort margin SHRANK from the dog's approach (s < 0.95, earned)",
          ctl.comfort.s < 0.95, "s=%.3f" % ctl.comfort.s)
    check("D_comfort stayed inside [D_MIN, D_MAX]",
          C.D_MIN_M - EPS <= ctl.comfort.d_comfort() <= C.D_MAX_M + EPS)

    cm = ComfortMargin()
    r2 = _Rng(3)
    for _ in range(2000):
        cm.update(r2.uniform(0.0, 0.3), tracked=r2.uniform(0, 1) > 0.3,
                  d=r2.uniform(0.0, 4.0), ddot=r2.uniform(-1.0, 1.0),
                  v_cmd=r2.uniform(-0.4, 0.4),
                  arousal_norm=r2.uniform(0.0, 1.0))
        if not (0.0 <= cm.s <= 1.0):
            break
    check("comfort fuzz: s bounded in [0,1] under 2000 random updates",
          0.0 <= cm.s <= 1.0)

    # ---------------------------------------------------------------- 5/5
    print("[selftest] 5/5 mode gating + fail-safe degradation (spec section 6)")
    sink = _RecSink()
    events = []
    ctl = MotionController(mobile_enabled=False, sink=sink,
                           on_reward=events.append)   # (5) OFF
    ctl.ingest_state({"exposure_level": 2})
    wld = _World(dog_xy=(3.0, 0.0), rng=_Rng(23))
    lines = []
    _run_sim(ctl, wld, 25.0, dog_vx=-0.15, dog_stop_at_d=0.9,
             on_tick=lambda now, w, line, v, om: lines.append(line))
    check("MOBILE OFF: tick() returned None every tick (no /motion deposit)",
          all(l is None for l in lines))
    check("MOBILE OFF: the sink was NEVER touched (no drive commands at all)",
          not sink.sets and sink.stops == 0)
    check("MOBILE OFF: stationary reward-on-approach still ran", len(events) >= 1)
    check("MOBILE OFF: estimator/telemetry stack still live",
          ctl.status(1000.0 + wld.t)["d_rd_m"] is not None)

    ctl = MotionController(mobile_enabled=True, sink=_RecSink())
    wld = _World(dog_xy=(4.0, 0.0), rng=_Rng(5))      # exposure never raised (default 0)
    ctl.nominal = lambda est: (0.30, 0.0)
    lines = []
    _run_sim(ctl, wld, 3.0,
             on_tick=lambda now, w, line, v, om: lines.append(line))
    check("exposure 0 (power-on default): every line is STOP, robot never moved",
          all(l == "STOP" for l in lines) and wld.x == 0.0)

    ctl = MotionController(mobile_enabled=True)
    ctl.ingest_state({"exposure_level": 1})
    wld = _World(dog_xy=(4.0, 0.0), rng=_Rng(9))
    ctl.nominal = lambda est: (0.30, 0.6)
    vs = []
    _run_sim(ctl, wld, 3.0,
             on_tick=lambda now, w, line, v, om: vs.append(v))
    check("exposure 1 (orient only): translation never commanded (v <= 0)",
          all(v <= EPS for v in vs))

    ctl = MotionController(mobile_enabled=True)
    ctl.ingest_state({"exposure_level": 2})
    ctl.nominal = lambda est: (0.30, 0.0)
    wld = _World(dog_xy=(6.0, 0.0), rng=_Rng(13))
    after = []

    def on_tick(now, world, line, v_cmd, w_cmd):
        if world.t > 5.0 + C.DET_HOLD_S + 0.3:
            after.append(v_cmd)

    _run_sim(ctl, wld, 12.0, det_until=5.0, on_tick=on_tick)
    check("DETECTION DROPOUT: past the hold, forward motion is refused (v <= 0)",
          after and all(v <= EPS for v in after), "%d ticks" % len(after))

    now_end = 1000.0 + wld.t
    line = ctl.tick(now=now_end + C.TELEM_STALE_S + 0.5)   # telemetry went silent
    check("TELEMETRY DROPOUT: stale estimate => STOP", line == "STOP")

    ctl.ingest_config({"mobile_enabled": 0})
    check("firmware /config announcing stationary DEGRADES the Pi to off (one-way)",
          ctl.tick(now=now_end + 2.0) is None and ctl.mobile is False)
    ctl.ingest_config({"mobile_enabled": 1, "cbf_gamma": 99.0,
                       "d_min_cm": 300, "d_max_cm": 100})
    check("announce cannot force mobile ON, absurd gamma/band rejected",
          ctl.mobile is False and ctl.gamma <= C.CBF_GAMMA_MAX
          and ctl.comfort.d_min < ctl.comfort.d_max)

    print("[selftest] %s" % ("PASS" if not fails else "FAIL: " + ", ".join(fails)))
    return not fails


def main():
    ap = argparse.ArgumentParser(
        description="CANIS Pi-side CBF motion controller (spec: "
                    "docs/SAFE_MOTION_CBF_SPEC.md; wire: docs/INTERFACE_CONTRACT.md)")
    ap.add_argument("--selftest", action="store_true",
                    help="run estimator+comfort+reward+CBF on synthetic data "
                         "(no hardware, no network) and assert the barrier "
                         "invariant; exits 0 on PASS")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(0 if run_selftest() else 1)
    ap.error("this module is a library (import MotionController from the "
             "dashboard); the only standalone action is --selftest")


if __name__ == "__main__":
    main()
