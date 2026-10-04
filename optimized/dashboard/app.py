"""
CANIS — Flask dashboard  [OPTIMIZED DUPLICATE]
==============================================
Same data flow and features as the original dashboard/app.py (live ESP32 view,
simulator fallback, Pi-side wall-clock stamping of last_reward_time), hardened
per the security/robustness review:

  [OPT-1] Tunables come from canis_config.py instead of being re-typed inline
          (orig app.py:29-33). Single source of truth on the Python side.
  [OPT-2] Shared-secret token required on ALL mutating routes (/ingest, /detect,
          /command). Token from the CANIS_TOKEN env var; accepted via the
          X-CANIS-Token header OR a ?token= query param. Requests without a
          valid token get 401. (orig had NO auth — anyone on the LAN could POST
          fake state or drive the robot, orig app.py:176-200.)
  [OPT-2] Input validation + bounds on /ingest (state enum, arousal range,
          non-negative bounded counters) and /detect (zone type/length). The
          original trusted whatever arrived (orig app.py:176-193).
  [OPT-2] Thread locks around the shared live/command/sim singletons. Flask's
          default server (and gunicorn/waitress with threads) handles requests
          concurrently, so the read-modify-write in ingest()/set()/snapshot()
          could interleave. (orig had no locking.)
  [OPT-14] Durable event log + study export. Every state transition, treat,
          jam, zone sighting, exposure change, firmware reboot, and link gap is
          appended to a JSONL event log (event_log.py; schema documented
          there). New routes:
            GET /export/events.csv     raw events, flattened to CSV
            GET /export/sessions.csv   per-session aggregates (the longitudinal
                                       study's data sheet — see
                                       optimized/study/STUDY_PROTOCOL.md)
            GET /api/events?limit=N    JSON tail for quick inspection
          Log directory: CANIS_LOG_DIR env var (default: <app dir>/logs).
          (orig kept ONLY the latest snapshot — the behavioral record vanished
          on refresh, which made a longitudinal study impossible to run.)
  [C1]    Runtime-config anti-drift. New POST /config: the ESP32 announces the
          tunables it is ACTUALLY running at boot, and the dashboard displays
          THAT (GET /api/config, source="firmware") instead of guessing from
          canis_config.py — which is now only the sim/offline fallback
          (source="fallback"). Kills the last drift class (ENGINEERING_REVIEW
          §6.3).
  [C4]    Two-step exposure arm/confirm (exposure_arm.ExposureArm), the
          dashboard mirror of the firmware's 'm'(arm)/'y'(confirm) defense. New
          mutating routes /exposure/arm, /exposure/confirm, /exposure/disarm and
          the read-only /api/session drive a VISIBLE session arm/disarm control,
          so the welfare-critical level can never flip on a lone click (§1.7).
  [MOBILE] Pi-side CBF controller wired in (control/motion_controller.py).
          Strictly additive: POST /telemetry (contract §1.2, token-auth like
          /ingest) feeds the estimator; GET /motion (contract §2.1, token like
          /command) serves the post-QP "MOVE <v> <omega>" or "STOP" from the
          `motion` singleton. ONE MotionController instance is fed by the
          existing /ingest, /detect and /config handlers and ticked by ONE
          daemon thread at CANIS_CONTROL_RATE_HZ. Fail-safe throughout: with
          CANIS_MOBILE_ENABLED=0 tick() returns None, the sink is never
          touched, /motion serves the literal "STOP", and every pre-existing
          route/behavior is unchanged; any controller exception logs and
          forces the motion sink to STOP.

Run (dev):        python3 app.py
Run (production): see optimized/dashboard/canis-dashboard.service (waitress)
"""

import csv
import io
import os
import sys
import time
import threading

from flask import Flask, Response, jsonify, render_template, request

import canis_config as cfg
import event_log as elog
import exposure_arm as xarm

app = Flask(__name__)

# ---- Tunables (single source of truth: canis_config.py) ----
AROUSAL_MAX      = cfg.AROUSAL_MAX
DECAY_PER_SEC    = cfg.DECAY_PER_SEC
TRIGGER_BUMP     = cfg.TRIGGER_BUMP
AROUSAL_ENTER    = cfg.AROUSAL_ENTER
AROUSAL_EXIT     = cfg.AROUSAL_EXIT
CORRECTION_ENTER = cfg.CORRECTION_ENTER
CORRECTION_EXIT  = cfg.CORRECTION_EXIT
STALE_AFTER      = cfg.STALE_AFTER
COMMAND_TTL      = cfg.COMMAND_TTL

# ---- Shared secret [OPT-2] ----
# Set in the environment (systemd EnvironmentFile / `export CANIS_TOKEN=...`).
# If unset, auth is DISABLED and a loud warning is logged — fine for a bench
# demo, never for anything exposed. The ESP32 sends the same token.
CANIS_TOKEN = os.environ.get("CANIS_TOKEN", "").strip()
if not CANIS_TOKEN:
    app.logger.warning(
        "CANIS_TOKEN is not set — mutating routes are UNAUTHENTICATED. "
        "Set CANIS_TOKEN in the environment for any non-bench deployment.")


def _token_ok():
    """Constant-time-ish check of the request token against CANIS_TOKEN."""
    if not CANIS_TOKEN:
        return True  # auth disabled (dev/bench)
    supplied = request.headers.get("X-CANIS-Token") or request.args.get("token", "")
    # hmac.compare_digest avoids leaking length/position via timing.
    import hmac
    return hmac.compare_digest(supplied, CANIS_TOKEN)


def _require_token():
    """Return a 401 response tuple if the token is missing/invalid, else None."""
    if not _token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


# ---- Event log [OPT-14] ----
LOG_DIR = os.environ.get("CANIS_LOG_DIR",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"))
EVENTS_PATH = os.path.join(LOG_DIR, cfg.EVENT_LOG_FILE)
events = elog.EventLog(EVENTS_PATH)
events.log("session_start")


# ---------------------------------------------------------------------------
# Live state from the ESP32 (thread-safe)
# ---------------------------------------------------------------------------
class LiveState:
    def __init__(self):
        self.data = None
        self.received_at = 0.0
        self._last_reward_count = 0
        self.last_reward_time = None
        self._lock = threading.Lock()  # [OPT-2] guard the read-modify-write

    def ingest(self, payload):
        now = time.time()
        with self._lock:
            prev = self.data  # previous snapshot, for change detection [OPT-14]

            # [OPT-14] the ESP32 went silent past the freshness window and is
            # back — record the outage so session data can be trusted/annotated.
            if prev is not None and (now - self.received_at) > STALE_AFTER:
                events.log("link_gap", gap_sec=round(now - self.received_at, 1))

            rc = _bounded_int(payload.get("reward_count", 0))
            if rc > self._last_reward_count:
                self.last_reward_time = now
                events.log("treat_dispensed", reward_count=rc)          # [OPT-14]
            self._last_reward_count = rc

            arousal = round(_bounded_float(payload.get("arousal", 0.0),
                                           cfg.AROUSAL_MIN, AROUSAL_MAX), 1)
            jam = _bounded_int(payload.get("jam_count", 0))
            exposure = _bounded_int(payload.get("exposure_level", 0), 0, 2)
            uptime = _bounded_int(payload.get("uptime_sec", 0))

            # [MOBILE] Optional mobile-drivetrain fields (INTERFACE_CONTRACT §1.1).
            # Strictly backward compatible: a legacy /ingest body omits all seven
            # and every one of these comes back None (stored as JSON null) —
            # never a fabricated 0. Present values are bounded like the rest.
            mobile = _opt_int(payload, "mobile", 0, 1)
            enc_l = _opt_int(payload, "enc_l", -cfg.ENC_ABS_MAX, cfg.ENC_ABS_MAX)
            enc_r = _opt_int(payload, "enc_r", -cfg.ENC_ABS_MAX, cfg.ENC_ABS_MAX)
            odom_v = _opt_float(payload, "odom_v",
                                -cfg.ODOM_V_ABS_MAX, cfg.ODOM_V_ABS_MAX, ndigits=3)
            odom_omega = _opt_float(payload, "odom_omega",
                                    -cfg.ODOM_W_ABS_MAX, cfg.ODOM_W_ABS_MAX, ndigits=3)
            tof_m = _clean_tof(payload.get("tof_m"))
            heading_deg = _opt_float(payload, "heading_deg", 0.0, 360.0, ndigits=1)
            if heading_deg is not None and heading_deg >= 360.0:  # contract: [0, 360)
                heading_deg = 0.0

            if prev is not None:                                         # [OPT-14]
                if payload["state"] != prev["state"]:
                    events.log("state_transition", **{"from": prev["state"],
                                                      "to": payload["state"],
                                                      "arousal": arousal})
                if jam > prev.get("jam_count", 0):
                    events.log("dispenser_jam", jam_count=jam)
                if exposure != prev.get("exposure_level", 0):
                    events.log("exposure_change",
                               **{"from": prev.get("exposure_level", 0), "to": exposure})
                if uptime < prev.get("uptime_sec", 0):
                    events.log("firmware_reboot", uptime_sec=uptime)

            self.data = {
                "state": payload["state"],  # already validated by caller
                "arousal": arousal,
                "arousal_max": _bounded_float(payload.get("arousal_max", AROUSAL_MAX),
                                              1.0, 100000.0),
                "intervention_count": _bounded_int(payload.get("intervention_count", 0)),
                "reward_count": rc,
                "jam_count": jam,
                "exposure_level": exposure,
                "last_reward_time": self.last_reward_time,
                "uptime_sec": uptime,
                # [MOBILE] contract §1.1: absent field => null, never a fake 0.
                "mobile": mobile,
                "enc_l": enc_l,
                "enc_r": enc_r,
                "odom_v": odom_v,
                "odom_omega": odom_omega,
                "tof_m": tof_m,
                "heading_deg": heading_deg,
                "source": "esp32",
                "ts": now,
            }
            self.received_at = now

    def snapshot(self):
        with self._lock:
            fresh = self.data is not None and (time.time() - self.received_at) < STALE_AFTER
            return dict(self.data) if fresh else None


live = LiveState()


# ---------------------------------------------------------------------------
# Input validation helpers [OPT-2]
# ---------------------------------------------------------------------------
def _bounded_float(v, lo, hi):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return lo
    if f != f:              # NaN
        return lo
    return max(lo, min(hi, f))


def _bounded_int(v, lo=0, hi=None):
    hi = cfg.COUNT_MAX if hi is None else hi
    try:
        i = int(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, i))


def _valid_zone(z):
    """A zone must be a short, printable string. Returns cleaned str or None."""
    if not isinstance(z, str):
        return None
    z = z.strip()
    if not z or len(z) > cfg.ZONE_MAX_LEN:
        return None
    if not z.isprintable():
        return None
    return z


# [MOBILE] Optional-field helpers: the new telemetry/detection fields are ALL
# optional (contract §1.1/§3). Missing or unparsable => None (JSON null), so a
# legacy sender can never trigger an error; present values are bounded.
def _opt_float(payload, key, lo, hi, ndigits=None):
    if key not in payload or payload[key] is None:
        return None
    try:
        f = float(payload[key])
    except (TypeError, ValueError):
        return None
    if f != f:              # NaN
        return None
    f = max(lo, min(hi, f))
    return round(f, ndigits) if ndigits is not None else f


def _opt_int(payload, key, lo, hi):
    if key not in payload or payload[key] is None:
        return None
    try:
        i = int(payload[key])
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, i))


def _clean_tof(v):
    """tof_m per contract §1.1: meters, -1.0 is the ONLY no-data sentinel.
    Missing/unparsable => None; any negative reading normalizes to -1.0."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:              # NaN
        return None
    if f < 0.0:
        return -1.0
    return round(min(f, cfg.TOF_MAX_M), 3)


def _valid_node_id(n):
    """node_id per contract §3: short printable string; None if absent/bad."""
    if not isinstance(n, str):
        return None
    n = n.strip()
    if not n or len(n) > cfg.NODE_ID_MAX_LEN or not n.isprintable():
        return None
    return n


# ---------------------------------------------------------------------------
# Fallback simulator (thread-safe) — mirrors the firmware, hysteresis included.
# ---------------------------------------------------------------------------
class Simulator:
    def __init__(self):
        self.arousal = 0.0
        self.state = "CALM"
        self.last_reward_time = None
        self.intervention_count = 0
        self.reward_count = 0
        self.started = time.time()
        self._last_tick = time.time()
        self._scripted = [(3, "trigger"), (4, "trigger"),
                          (6, "trigger"), (14, "reset")]
        self._lock = threading.Lock()  # [OPT-2]

    def _classify(self, current, a):
        # [OPT-3] mirror the firmware Schmitt trigger so the demo matches reality.
        if current == "CALM":
            if a >= CORRECTION_ENTER:
                return "CORRECTION"
            if a >= AROUSAL_ENTER:
                return "AROUSAL"
            return "CALM"
        if current == "AROUSAL":
            if a >= CORRECTION_ENTER:
                return "CORRECTION"
            if a < AROUSAL_EXIT:
                return "CALM"
            return "AROUSAL"
        # CORRECTION
        if a < AROUSAL_EXIT:
            return "CALM"
        if a < CORRECTION_EXIT:
            return "AROUSAL"
        return "CORRECTION"

    def snapshot(self):
        now = time.time()
        with self._lock:
            dt = now - self._last_tick
            self._last_tick = now

            phase = (now - self.started) % 20
            for at, kind in self._scripted:
                if abs(phase - at) < dt:
                    if kind == "trigger":
                        self.arousal = min(AROUSAL_MAX, self.arousal + TRIGGER_BUMP)
                    elif kind == "reset":
                        self.arousal = 0.0

            self.arousal = max(0.0, self.arousal - DECAY_PER_SEC * dt)

            new_state = self._classify(self.state, self.arousal)
            if new_state != self.state:
                if new_state == "CORRECTION":
                    self.intervention_count += 1
                if new_state == "CALM" and self.state in ("AROUSAL", "CORRECTION"):
                    self.reward_count += 1
                    self.last_reward_time = now
                self.state = new_state

            return {
                "state": self.state,
                "arousal": round(self.arousal, 1),
                "arousal_max": AROUSAL_MAX,
                "intervention_count": self.intervention_count,
                "reward_count": self.reward_count,
                "jam_count": 0,          # keep the schema identical to the live path
                "exposure_level": 0,
                "last_reward_time": self.last_reward_time,
                "uptime_sec": round(now - self.started),
                # [MOBILE] schema parity with the live path: the sim is a
                # stationary bench robot, so the mobile fields stay null.
                "mobile": 0,
                "enc_l": None,
                "enc_r": None,
                "odom_v": None,
                "odom_omega": None,
                "tof_m": None,
                "heading_deg": None,
                "source": "sim",
                "ts": now,
            }


sim = Simulator()


# ---------------------------------------------------------------------------
# Vision -> robot command channel (thread-safe)
# ---------------------------------------------------------------------------
class Command:
    def __init__(self):
        self.zone = None
        self.at = 0.0
        self.meta = {}  # [MOBILE] optional YOLO fields from the last /detect
        self._lock = threading.Lock()  # [OPT-2]

    def set(self, zone, meta=None):
        with self._lock:
            self.zone, self.at = zone, time.time()
            self.meta = dict(meta) if meta else {}

    def current(self):
        with self._lock:
            if self.zone and (time.time() - self.at) < COMMAND_TTL:
                return self.zone
            return None

    def detection(self):
        """[MOBILE] Snapshot of the last detection for /api/detection.
        `active` mirrors the /command TTL; the last sighting stays visible
        (with its age) after the TTL so the panel isn't blank between passes."""
        with self._lock:
            if self.zone is None:
                return {"zone": None, "active": False, "age_s": None,
                        "dog_present": None, "bearing_deg": None,
                        "distance_m": None, "confidence": None,
                        "node_id": None, "ts": None}
            age = time.time() - self.at
            out = {"zone": self.zone, "active": age < COMMAND_TTL,
                   "age_s": round(age, 1)}
            for k in ("dog_present", "bearing_deg", "distance_m",
                      "confidence", "node_id", "ts"):
                out[k] = self.meta.get(k)  # absent => null, per contract
            return out


command = Command()


# ---------------------------------------------------------------------------
# [MOBILE] High-rate motion telemetry (INTERFACE_CONTRACT §1.2)
# The ESP32 streams /telemetry at CANIS_ODOM_STREAM_HZ (10 Hz) when mobile.
# Freshest-only: we keep the last frame, nothing is queued, and frames are NOT
# written to the event log (10 Hz would flood the JSONL study record).
# ---------------------------------------------------------------------------
class Telemetry:
    def __init__(self):
        self.frame = None
        self.received_at = 0.0
        self._lock = threading.Lock()

    def set(self, frame):
        with self._lock:
            self.frame = frame
            self.received_at = time.time()

    def snapshot(self):
        with self._lock:
            fresh = (self.frame is not None and
                     (time.time() - self.received_at) < cfg.TELEMETRY_STALE_AFTER)
            frame = dict(self.frame) if self.frame is not None else None
            return frame, fresh, (self.received_at or None)


telemetry = Telemetry()


# ---------------------------------------------------------------------------
# [MOBILE] Motion command channel (INTERFACE_CONTRACT §2.1)
# GET /motion serves the post-QP (v, omega) that the Pi-side CBF controller
# deposits via motion.set(). Until that controller runs (or whenever its
# command is older than MOTION_CMD_TTL_MS), /motion serves the literal "STOP"
# — the safe default the contract requires. Values are clamped to the firmware
# caps here as well (defense in depth; the ESP32 clamps again).
# ---------------------------------------------------------------------------
class MotionCommand:
    TTL_S = cfg.MOTION_CMD_TTL_MS / 1000.0

    def __init__(self):
        self.v = 0.0
        self.omega = 0.0
        self.at = 0.0
        self._lock = threading.Lock()

    def set(self, v, omega):
        """Deposit a fresh post-QP u* = (v, omega). Called by the Pi-side CBF
        controller (in-process); NOT exposed as an HTTP route — /motion is the
        only motion endpoint in the contract."""
        with self._lock:
            self.v = _bounded_float(v, -cfg.MAX_LINEAR_MPS, cfg.MAX_LINEAR_MPS)
            self.omega = _bounded_float(omega, -cfg.MAX_ANGULAR_RPS, cfg.MAX_ANGULAR_RPS)
            self.at = time.time()

    def stop(self):
        with self._lock:
            self.at = 0.0  # expire immediately => /motion serves STOP

    def line(self):
        """The exact one-line text/plain body for GET /motion."""
        with self._lock:
            if self.at and (time.time() - self.at) < self.TTL_S:
                return "MOVE %.3f %.3f" % (self.v, self.omega)
            return "STOP"


motion = MotionCommand()


# ---------------------------------------------------------------------------
# [MOBILE] Pi-side CBF motion controller (control/motion_controller.py)
# ONE instance for the whole process. Its sink is the `motion` singleton above
# (duck-typed .set(v, w)/.stop()); GET /motion only ever reads motion.line().
# Import failure is non-fatal: the dashboard stays fully functional and
# /motion serves "STOP" forever — the stationary study is never blocked by a
# missing/broken control package.
# ---------------------------------------------------------------------------
try:
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from control.motion_controller import MotionController
    controller = MotionController(sink=motion)
except Exception as _exc:                    # noqa: BLE001 — fail safe, not loud
    controller = None
    app.logger.warning(
        "[MOBILE] CBF controller unavailable (%s) — /motion will serve STOP.",
        _exc)


def _controller_feed(method_name, payload):
    """Feed one ingest_* call to the controller, exception-proofed. A broken
    controller must never break a route OR leave a live MOVE being served, so
    on any exception we log and force the motion sink to STOP (fail safe).
    Called AFTER the dashboard singletons have done their own locked work —
    no dashboard lock is held here, so no lock-ordering deadlock is possible
    (the controller's RLock is the only lock ever taken inside)."""
    if controller is None:
        return
    try:
        getattr(controller, method_name)(payload)
    except Exception:                        # noqa: BLE001
        app.logger.exception(
            "[MOBILE] controller.%s failed — motion sink -> STOP", method_name)
        try:
            motion.stop()
        except Exception:                    # noqa: BLE001
            pass


# Tick rate: CANIS_CONTROL_RATE_HZ env override, else the canis_config.py
# mirror, else 20 Hz — bounded to something sane either way.
try:
    CONTROL_RATE_HZ = int(os.environ.get("CANIS_CONTROL_RATE_HZ",
                                         getattr(cfg, "CONTROL_RATE_HZ", 20)))
except (TypeError, ValueError):
    CONTROL_RATE_HZ = 20
if not (1 <= CONTROL_RATE_HZ <= 200):
    CONTROL_RATE_HZ = 20


def _controller_tick_loop():
    """ONE daemon thread driving controller.tick() at CONTROL_RATE_HZ.
    tick() itself deposits the post-QP u* into `motion` via the sink
    (.set on MOVE, .stop on hold) and returns None in stationary mode
    WITHOUT touching the sink — so with CANIS_MOBILE_ENABLED=0 the motion
    singleton stays at its power-on STOP forever. A tick exception is
    logged, the sink is forced to STOP, and the loop continues: a
    controller bug can never kill the dashboard or leave a stale MOVE."""
    period = 1.0 / float(CONTROL_RATE_HZ)
    while True:
        started = time.monotonic()
        try:
            controller.tick()
        except Exception:                    # noqa: BLE001
            app.logger.exception(
                "[MOBILE] controller.tick() raised — motion sink -> STOP")
            try:
                motion.stop()
            except Exception:                # noqa: BLE001
                pass
        time.sleep(max(0.0, period - (time.monotonic() - started)))


if controller is not None:
    _tick_thread = threading.Thread(target=_controller_tick_loop,
                                    name="canis-cbf-tick", daemon=True)
    _tick_thread.start()


# ---------------------------------------------------------------------------
# Firmware-reported config [C1] — anti-drift
# The ESP32 POSTs the tunables it is ACTUALLY running to /config at boot; the
# dashboard then shows firmware truth instead of guessing from canis_config.py
# (which becomes the sim/offline fallback only).
# ---------------------------------------------------------------------------
# Whitelist of numeric keys accepted from /config; anything else is dropped so a
# malformed/oversized POST can't bloat memory. "fw" is a short string tag.
_CONFIG_NUM_KEYS = (
    "observe_only", "exposure_level", "exposure_default",
    "arousal_max", "decay_per_sec", "rise_per_sec", "dt_max_s",
    "arousal_enter", "arousal_exit", "correction_enter", "correction_exit",
    "reward_cooldown_ms", "calm_reward_ms", "max_session_rewards",
    "vision_ttl_ms", "jam_fault_threshold", "use_dispense_ack", "ack_timeout_ms",
    # [MOBILE] contract §3.1 — mobile drivetrain + CBF announce keys
    "mobile_enabled", "d_min_cm", "d_max_cm", "cbf_gamma", "control_hz",
    "odom_hz", "motion_cmd_ttl_ms", "max_v_mps", "max_w_rps",
)


def _sanitize_config(payload):
    """Keep only known keys, coerced to safe scalars (numbers + a short fw tag).
    Integers stay integers so the panel doesn't show 30.0 for a count."""
    out = {}
    fw = payload.get("fw")
    if isinstance(fw, str):
        out["fw"] = fw[:40]
    for k in _CONFIG_NUM_KEYS:
        if k in payload:
            v = _bounded_float(payload[k], -1e12, 1e12)
            out[k] = int(v) if float(v).is_integer() else round(v, 3)
    return out


def _fallback_config():
    """The canis_config.py mirror, in the SAME shape the firmware posts, so the
    panel is never blank before an ESP32 checks in — clearly labeled as the
    fallback so it is not mistaken for firmware truth."""
    return {
        "fw": "canis_config.py (fallback)",
        "observe_only": cfg.OBSERVE_ONLY,
        "exposure_default": cfg.EXPOSURE_DEFAULT,
        "arousal_max": cfg.AROUSAL_MAX,
        "decay_per_sec": cfg.DECAY_PER_SEC,
        "rise_per_sec": cfg.AROUSAL_RISE_PER_SEC,
        "dt_max_s": cfg.DT_MAX_S,
        "arousal_enter": cfg.AROUSAL_ENTER,
        "arousal_exit": cfg.AROUSAL_EXIT,
        "correction_enter": cfg.CORRECTION_ENTER,
        "correction_exit": cfg.CORRECTION_EXIT,
        "reward_cooldown_ms": cfg.REWARD_COOLDOWN_MS,
        "calm_reward_ms": cfg.CALM_REWARD_MS,
        "max_session_rewards": cfg.MAX_SESSION_REWARDS,
        "vision_ttl_ms": cfg.VISION_TTL_MS,
        "jam_fault_threshold": cfg.JAM_FAULT_THRESHOLD,
        # [MOBILE] contract §3.1 mirror
        "mobile_enabled": cfg.MOBILE_ENABLED,
        "d_min_cm": cfg.D_MIN_CM,
        "d_max_cm": cfg.D_MAX_CM,
        "cbf_gamma": cfg.CBF_GAMMA,
        "control_hz": cfg.CONTROL_RATE_HZ,
        "odom_hz": cfg.ODOM_STREAM_HZ,
        "motion_cmd_ttl_ms": cfg.MOTION_CMD_TTL_MS,
        "max_v_mps": cfg.MAX_LINEAR_MPS,
        "max_w_rps": cfg.MAX_ANGULAR_RPS,
    }


class FirmwareConfig:
    """Thread-safe holder for the last config the ESP32 announced [C1]."""

    def __init__(self):
        self.data = None
        self.received_at = 0.0
        self._lock = threading.Lock()

    def set(self, sanitized):
        with self._lock:
            self.data = sanitized
            self.received_at = time.time()

    def view(self):
        """(source, config, received_at): firmware truth if any has posted, else
        the canis_config.py fallback."""
        with self._lock:
            if self.data is not None:
                return "firmware", dict(self.data), self.received_at
        return "fallback", _fallback_config(), None


fwconfig = FirmwareConfig()

# Two-step exposure arm/confirm [C4]; window mirrors the firmware's define.
exposure = xarm.ExposureArm(confirm_s=cfg.EXPOSURE_CONFIRM_MS / 1000.0,
                            authorized_level=cfg.EXPOSURE_DEFAULT)


# ---------------------------------------------------------------------------
# [MOBILE] Display-only comfort margin / CBF status derivation.
# The AUTHORITATIVE D_comfort(s) estimator and barrier QP run in the Pi-side
# controller (docs/SAFE_MOTION_CBF_SPEC.md §4-§5); nothing here actuates. This
# is the dashboard's human-readable approximation: stress s = arousal/max,
# linearly mapped onto [D_MIN, D_MAX] (using firmware-announced d_min_cm/
# d_max_cm when available), margin = tof_m - D_comfort.
# ---------------------------------------------------------------------------
CBF_BOUNDARY_BAND_M = 0.15  # display band around h = 0 labeled "BOUNDARY"


def _comfort_view(snap):
    """Return {mode, d_comfort_m, comfort_margin_m, cbf_status} for a snapshot."""
    mobile = snap.get("mobile")
    mode = "mobile" if mobile == 1 else "stationary"
    if mobile != 1:
        # Stationary (or legacy firmware that omits `mobile`): the CBF layer
        # is not in play. Distance may still exist via /detect distance_m.
        return {"mode": mode, "d_comfort_m": None,
                "comfort_margin_m": None, "cbf_status": "STATIONARY"}

    _, conf, _ = fwconfig.view()  # firmware truth if posted, else fallback
    d_min = _bounded_float(conf.get("d_min_cm", cfg.D_MIN_CM), 1.0, 10000.0) / 100.0
    d_max = _bounded_float(conf.get("d_max_cm", cfg.D_MAX_CM), 1.0, 10000.0) / 100.0
    if d_max < d_min:
        d_max = d_min

    tof = snap.get("tof_m")
    if tof is None or tof < 0.0:
        # Sensor dropout: the spec treats d_rd as at-the-boundary (fail-safe
        # hold, no distance-reducing motion). Show that state explicitly.
        return {"mode": mode, "d_comfort_m": None,
                "comfort_margin_m": None, "cbf_status": "NO-RANGE (fail-safe hold)"}

    a_max = snap.get("arousal_max") or AROUSAL_MAX
    stress = _bounded_float(snap.get("arousal", 0.0), 0.0, a_max) / a_max
    d_comfort = d_min + stress * (d_max - d_min)
    margin = tof - d_comfort
    if margin < 0.0:
        status = "VIOLATION"
    elif margin < CBF_BOUNDARY_BAND_M:
        status = "BOUNDARY"
    else:
        status = "SAFE"
    return {"mode": mode, "d_comfort_m": round(d_comfort, 2),
            "comfort_margin_m": round(margin, 2), "cbf_status": status}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    # Read-only, public: the dashboard page fetches this. No token required.
    snap = live.snapshot()
    if snap is None:
        snap = sim.snapshot()
    # [MOBILE] additive, derived, display-only keys — existing consumers that
    # ignore unknown keys are unaffected.
    snap.update(_comfort_view(snap))
    snap["motion_cmd"] = motion.line()
    return jsonify(snap)


@app.route("/ingest", methods=["POST"])
def ingest():
    denied = _require_token()               # [OPT-2]
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    state = payload.get("state")
    if state not in cfg.VALID_STATES:       # [OPT-2] validate enum
        return jsonify({"ok": False, "error": "missing/invalid 'state'"}), 400
    payload["state"] = state
    live.ingest(payload)                     # bounds applied inside ingest()
    # [MOBILE] exposure gate + arousal for the comfort margin (contract §1.1).
    # After live.ingest() returned — its lock is released; feed is additive
    # and exception-proofed, so the legacy response is untouched.
    _controller_feed("ingest_state", payload)
    return jsonify({"ok": True})


@app.route("/detect", methods=["POST"])
def detect():
    denied = _require_token()               # [OPT-2]
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    zone = _valid_zone(payload.get("zone"))  # [OPT-2] validate zone
    if zone is None:
        return jsonify({"ok": False, "error": "missing/invalid 'zone'"}), 400

    # [MOBILE] Optional YOLO fields (INTERFACE_CONTRACT §3). All optional —
    # the legacy ArUco {"zone": ...} body stays valid forever. Present values
    # are bounded; unparsable ones are dropped (never a 400), EXCEPT
    # dog_present, which the contract forbids sending as anything but true
    # (silence means "no dog") — a false would set a phantom command zone.
    if "dog_present" in payload and payload["dog_present"] is not True:
        return jsonify({"ok": False,
                        "error": "'dog_present' must be true when present"
                                 " (omit the POST to report no dog)"}), 400
    meta = {
        "dog_present": True if "dog_present" in payload else None,
        "bearing_deg": _opt_float(payload, "bearing_deg",
                                  -cfg.BEARING_ABS_MAX, cfg.BEARING_ABS_MAX,
                                  ndigits=1),
        "distance_m": _opt_float(payload, "distance_m", 0.0, cfg.TOF_MAX_M,
                                 ndigits=3),
        "confidence": _opt_float(payload, "confidence", 0.0, 1.0, ndigits=3),
        "node_id": _valid_node_id(payload.get("node_id")),
        "ts": _opt_float(payload, "ts", 0.0, 4e12),
    }

    command.set(zone, meta)
    # [MOBILE] bearing/distance for the estimator (contract §3). A zone-only
    # legacy body passes the all-None meta — presence only, no fabricated
    # range (the controller's fail-safe handles that case by design).
    _controller_feed("ingest_detection", meta)
    # [OPT-14] raw sighting (~1 Hz while visible), now with the YOLO extras
    # (contract §3: "logs them on the existing zone_seen event"). Nulls are
    # skipped so legacy ArUco rows stay byte-identical to before.
    extras = {k: v for k, v in meta.items() if v is not None}
    events.log("zone_seen", zone=zone, **extras)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Bark / "he went quiet" events from the Pi's audio detector (INTERFACE_CONTRACT §5)
# ---------------------------------------------------------------------------
class BarkState:
    """Latest events from audio/detect_bark.py.

    quiet_seq is an EPOCH-SECONDS counter (strictly increasing even across a dashboard restart), carried to
    the ESP32 in the X-CANIS-Quiet-Seq header of GET /command (with X-CANIS-Quiet-Age, seconds since it happened, so a late poll can never fire a stale treat). The ESP32 treats the first value it sees after
    boot as a baseline and rewards only when the value later INCREASES, so a restart or a reconnect can never
    replay an old treat."""

    VALID = ("bark", "quiet", "mic_lost", "mic_ok")

    def __init__(self):
        self._lock = threading.Lock()
        self.quiet_seq = 0
        self.quiet_ts = None        # when the last ACCEPTED quiet happened
        self.mic_ok = None          # None = never heard from the detector
        self.last_bark_ts = None
        self.barks_total = 0
        self.last_event = None

    def apply(self, event, now=None):
        """Apply a validated event. Returns True if it was a 'quiet' that was accepted."""
        now = time.time() if now is None else now
        with self._lock:
            self.last_event = event
            if event == "bark":
                self.last_bark_ts = round(now, 3)
                self.barks_total += 1
                self.mic_ok = True
            elif event == "mic_lost":
                self.mic_ok = False
            elif event == "mic_ok":
                self.mic_ok = True
            elif event == "quiet":
                if self.mic_ok is False:      # silence from a dead mic is not a quiet dog
                    return False
                self.mic_ok = True
                self.quiet_seq = max(int(now), self.quiet_seq + 1)
                self.quiet_ts = now
                return True
        return False

    def snapshot(self):
        with self._lock:
            age = round(time.time() - self.quiet_ts, 1) if self.quiet_ts else -1.0
            return {"quiet_seq": self.quiet_seq, "quiet_age_s": age, "mic_ok": self.mic_ok,
                    "last_bark_ts": self.last_bark_ts, "barks_total": self.barks_total,
                    "last_event": self.last_event}


bark = BarkState()


@app.route("/bark", methods=["POST"])
def post_bark():
    denied = _require_token()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    event = payload.get("event")
    if event not in BarkState.VALID:
        return jsonify({"ok": False, "error": "missing/invalid 'event'"}), 400
    accepted = bark.apply(event)
    # [OPT-14] study log: raw bark times and the quiet events that earned a treat.
    if event == "bark":
        events.log("bark",
                   duration_s=_opt_float(payload, "duration_s", 0.0, 30.0, ndigits=2),
                   peak_db=_opt_float(payload, "peak_db", -120.0, 6.0, ndigits=1))
    elif event == "quiet":
        events.log("bark_quiet", barks=int(_opt_float(payload, "barks", 0, 1000) or 0), accepted=accepted)
    else:
        events.log("mic_status", ok=(event == "mic_ok"))
    return jsonify({"ok": True, "accepted": accepted})


@app.route("/api/bark")
def api_bark():
    return jsonify(bark.snapshot())          # read-only, public like /api/detection


@app.route("/api/detection")
def api_detection():
    # Read-only, public (like /api/state): the page's YOLO detection panel.
    # /command (ESP32-facing) is untouched — still a bare text zone label.
    return jsonify(command.detection())


@app.route("/command")
def get_command():
    # Drives the robot -> treat as mutating-adjacent; require the token. [OPT-2]
    denied = _require_token()
    if denied:
        return "UNAUTHORIZED", 401, {"Content-Type": "text/plain"}
    z = command.current()
    b = bark.snapshot()
    return (z if z else "NONE"), 200, {"Content-Type": "text/plain",
                                      "X-CANIS-Quiet-Seq": str(b["quiet_seq"]),
                                      "X-CANIS-Quiet-Age": str(b["quiet_age_s"])}


# ---------------------------------------------------------------------------
# [MOBILE] High-rate motion loop routes (INTERFACE_CONTRACT §1.2 / §2.1)
# ---------------------------------------------------------------------------
@app.route("/telemetry", methods=["POST"])
def telemetry_ingest():
    """Contract §1.2: ESP32 streams {ts_ms, enc_l, enc_r, odom_v, odom_omega,
    tof_m, heading_deg} at CANIS_ODOM_STREAM_HZ when mobile. Token-auth like
    /ingest. EVERY field is optional (missing/unparsable => None, never a
    400 and never a fabricated 0) so a partial or legacy sender can't error;
    present values are bounded like the §1.1 mirrors in LiveState.ingest()."""
    denied = _require_token()               # [OPT-2] mutating -> token required
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    heading = _opt_float(payload, "heading_deg", 0.0, 360.0, ndigits=1)
    if heading is not None and heading >= 360.0:    # contract: [0, 360)
        heading = 0.0
    frame = {
        "ts_ms": _opt_int(payload, "ts_ms", 0, (1 << 32) - 1),
        "enc_l": _opt_int(payload, "enc_l", -cfg.ENC_ABS_MAX, cfg.ENC_ABS_MAX),
        "enc_r": _opt_int(payload, "enc_r", -cfg.ENC_ABS_MAX, cfg.ENC_ABS_MAX),
        "odom_v": _opt_float(payload, "odom_v",
                             -cfg.ODOM_V_ABS_MAX, cfg.ODOM_V_ABS_MAX, ndigits=3),
        "odom_omega": _opt_float(payload, "odom_omega",
                                 -cfg.ODOM_W_ABS_MAX, cfg.ODOM_W_ABS_MAX,
                                 ndigits=3),
        "tof_m": _clean_tof(payload.get("tof_m")),
        "heading_deg": heading,
    }
    telemetry.set(frame)                     # freshest-only holder (no log flood)
    _controller_feed("ingest_telemetry", frame)
    return jsonify({"ok": True})


@app.route("/motion")
def get_motion():
    """Contract §2.1: ONE text/plain line, 'MOVE <v> <omega>' or 'STOP',
    polled by the ESP32 at CANIS_ODOM_STREAM_HZ. Token check identical to
    /command (drives the robot). motion.line() already serves 'STOP' whenever
    mobile is off, no controller ever deposited, the last deposit is older
    than MOTION_CMD_TTL_MS, or the post-QP output is a hold — the safe
    default requires no controller at all."""
    denied = _require_token()               # [OPT-2] same posture as /command
    if denied:
        return "UNAUTHORIZED", 401, {"Content-Type": "text/plain"}
    return motion.line(), 200, {"Content-Type": "text/plain"}


# ---------------------------------------------------------------------------
# Runtime config anti-drift [C1]
# ---------------------------------------------------------------------------
@app.route("/config", methods=["POST"])
def config_ingest():
    denied = _require_token()               # [OPT-2] mutating -> token required
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not payload:
        return jsonify({"ok": False, "error": "empty/invalid config"}), 400
    clean = _sanitize_config(payload)
    if not clean:
        return jsonify({"ok": False, "error": "no known config keys"}), 400
    fwconfig.set(clean)
    # [MOBILE] runtime anti-drift for the controller too (contract §3.1):
    # announced mobile_enabled can only DEGRADE toward off; gamma/band are
    # adopted within conservative bounds inside ingest_config().
    _controller_feed("ingest_config", clean)
    events.log("firmware_config", fw=clean.get("fw", ""),
               observe_only=clean.get("observe_only"),
               exposure_level=clean.get("exposure_level"))
    return jsonify({"ok": True, "stored_keys": sorted(clean)})


@app.route("/api/config")
def api_config():
    # Read-only, public (like /api/state): displays firmware truth or fallback.
    source, conf, received = fwconfig.view()
    return jsonify({"source": source, "config": conf, "received_at": received})


# ---------------------------------------------------------------------------
# Two-step exposure arm/confirm + visible session state [C4]
# ---------------------------------------------------------------------------
def _req_level():
    """Parse an integer 'level' from the JSON body, or None if absent/bad."""
    payload = request.get_json(silent=True) or {}
    try:
        return int(payload.get("level"))
    except (TypeError, ValueError):
        return None


@app.route("/exposure/arm", methods=["POST"])
def exposure_arm_route():
    denied = _require_token()
    if denied:
        return denied
    lvl = _req_level()
    ok, msg = exposure.arm(lvl, time.time()) if lvl is not None else (False, "missing 'level'")
    return jsonify({"ok": ok, "msg": msg, "session": exposure.status(time.time())}), \
        (200 if ok else 400)


@app.route("/exposure/confirm", methods=["POST"])
def exposure_confirm_route():
    denied = _require_token()
    if denied:
        return denied
    lvl = _req_level()
    ok, msg = exposure.confirm(lvl, time.time()) if lvl is not None else (False, "missing 'level'")
    if ok:
        # Record the operator-authorized change (source distinguishes it from the
        # firmware-reported exposure_change derived in ingest()).
        events.log("exposure_change", **{"from": "operator-arm", "to": lvl, "source": "dashboard"})
    return jsonify({"ok": ok, "msg": msg, "session": exposure.status(time.time())}), \
        (200 if ok else 400)


@app.route("/exposure/disarm", methods=["POST"])
def exposure_disarm_route():
    denied = _require_token()
    if denied:
        return denied
    ok, msg = exposure.disarm()
    return jsonify({"ok": ok, "msg": msg, "session": exposure.status(time.time())})


@app.route("/api/session")
def api_session():
    # Read-only, public (the page polls this for the arm banner).
    return jsonify(exposure.status(time.time()))


# ---------------------------------------------------------------------------
# Study export routes [OPT-14]
# Read-only, like /api/state. They expose behavioral counters, not secrets.
# ---------------------------------------------------------------------------
def _load_events():
    try:
        return elog.read_events(EVENTS_PATH)
    except FileNotFoundError:
        return []


@app.route("/api/events")
def api_events():
    """JSON tail of the event log (default: last 100 events)."""
    limit = max(1, min(1000, request.args.get("limit", 100, type=int)))
    evts = _load_events()
    return jsonify({"count": len(evts), "events": evts[-limit:]})


@app.route("/export/events.csv")
def export_events_csv():
    """Every logged event, flattened to CSV (columns: event_log.EVENT_CSV_FIELDS)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=elog.EVENT_CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for evt in _load_events():
        w.writerow(evt)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=canis_events.csv"})


@app.route("/export/sessions.csv")
def export_sessions_csv():
    """Per-session aggregates — the longitudinal study's data sheet."""
    summaries = elog.summarize_sessions(_load_events(), zone_gap_s=cfg.ZONE_EPISODE_GAP_S)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=elog.SESSION_CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for s in summaries:
        w.writerow(s)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=canis_sessions.csv"})


if __name__ == "__main__":
    # host=0.0.0.0 so the ESP32 and your phone on the same WiFi can reach it.
    # For production use waitress via the systemd unit, not this dev server.
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("CANIS_DEBUG") == "1")
