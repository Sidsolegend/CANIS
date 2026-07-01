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

Run (dev):        python3 app.py
Run (production): see optimized/dashboard/canis-dashboard.service (waitress)
"""

import os
import time
import threading

from flask import Flask, jsonify, render_template, request

import canis_config as cfg

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
            rc = _bounded_int(payload.get("reward_count", 0))
            if rc > self._last_reward_count:
                self.last_reward_time = now
            self._last_reward_count = rc

            self.data = {
                "state": payload["state"],  # already validated by caller
                "arousal": round(_bounded_float(payload.get("arousal", 0.0),
                                                cfg.AROUSAL_MIN, AROUSAL_MAX), 1),
                "arousal_max": _bounded_float(payload.get("arousal_max", AROUSAL_MAX),
                                              1.0, 100000.0),
                "intervention_count": _bounded_int(payload.get("intervention_count", 0)),
                "reward_count": rc,
                "last_reward_time": self.last_reward_time,
                "uptime_sec": _bounded_int(payload.get("uptime_sec", 0)),
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
    return jsonify({"ok": True})


@app.route("/command")
def get_command():
    # Drives the robot -> treat as mutating-adjacent; require the token. [OPT-2]
    denied = _require_token()
    if denied:
        return "UNAUTHORIZED", 401, {"Content-Type": "text/plain"}
    z = command.current()
    return (z if z else "NONE"), 200, {"Content-Type": "text/plain"}


if __name__ == "__main__":
    # host=0.0.0.0 so the ESP32 and your phone on the same WiFi can reach it.
    # For production use waitress via the systemd unit, not this dev server.
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("CANIS_DEBUG") == "1")
