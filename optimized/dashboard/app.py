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

Run (dev):        python3 app.py
Run (production): see optimized/dashboard/canis-dashboard.service (waitress)
"""

import csv
import io
import os
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
        self._lock = threading.Lock()  # [OPT-2]

    def set(self, zone):
        with self._lock:
            self.zone, self.at = zone, time.time()

    def current(self):
        with self._lock:
            if self.zone and (time.time() - self.at) < COMMAND_TTL:
                return self.zone
            return None


command = Command()


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
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    # Read-only, public: the dashboard page fetches this. No token required.
    snap = live.snapshot()
    return jsonify(snap if snap is not None else sim.snapshot())


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
    command.set(zone)
    events.log("zone_seen", zone=zone)       # [OPT-14] raw sighting (~1 Hz while visible)
    return jsonify({"ok": True})


@app.route("/command")
def get_command():
    # Drives the robot -> treat as mutating-adjacent; require the token. [OPT-2]
    denied = _require_token()
    if denied:
        return "UNAUTHORIZED", 401, {"Content-Type": "text/plain"}
    z = command.current()
    return (z if z else "NONE"), 200, {"Content-Type": "text/plain"}


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
