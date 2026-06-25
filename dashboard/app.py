"""
CANIS — Flask dashboard
-----------------------
Live view of the robot's brain: current state, arousal level, reward history,
intervention count. Runs on the Pi.

DATA FLOW
  ESP32 (state machine)  --HTTP POST /ingest-->  this Pi  --serves /api/state-->  browser

The ESP32 pushes its raw state to /ingest. We stash the latest payload and
the dashboard serves it from /api/state. If no ESP32 data has arrived recently
(STALE_AFTER seconds), we fall back to the built-in simulator so the page stays
alive for demos. The "source" field tells you which one you're looking at.

The ESP32 has no wall clock, so it does NOT send timestamps. It sends counters
(reward_count, intervention_count). We watch reward_count climb and stamp
last_reward_time with the Pi's clock here — keeping all time-of-day concerns on
the device that actually knows the time.

Run:  python3 app.py    then open http://<pi-ip>:5000 from your phone.
"""

import time
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ---- Tunables mirrored from State_Machine_CANIS.ino (keep in sync) ----
AROUSAL_MAX      = 100.0
DECAY_PER_SEC    = 12.0
TRIGGER_BUMP     = 25.0
AROUSAL_ENTER    = 20.0
CORRECTION_ENTER = 70.0

STALE_AFTER = 5.0   # seconds without an ESP32 POST before we fall back to sim


# ---------------------------------------------------------------------------
# Live state from the ESP32
# ---------------------------------------------------------------------------
class LiveState:
    """Holds the most recent payload the ESP32 POSTed to /ingest."""

    def __init__(self):
        self.data = None            # last merged snapshot dict
        self.received_at = 0.0      # Pi clock when it arrived
        self._last_reward_count = 0
        self.last_reward_time = None

    def ingest(self, payload):
        now = time.time()
        # Stamp last_reward_time when the ESP32's reward counter ticks up.
        rc = int(payload.get("reward_count", 0))
        if rc > self._last_reward_count:
            self.last_reward_time = now
        self._last_reward_count = rc

        self.data = {
            "state": payload.get("state", "CALM"),
            "arousal": round(float(payload.get("arousal", 0.0)), 1),
            "arousal_max": float(payload.get("arousal_max", AROUSAL_MAX)),
            "intervention_count": int(payload.get("intervention_count", 0)),
            "reward_count": rc,
            "last_reward_time": self.last_reward_time,
            "uptime_sec": int(payload.get("uptime_sec", 0)),
            "source": "esp32",
            "ts": now,
        }
        self.received_at = now

    def fresh(self):
        return self.data is not None and (time.time() - self.received_at) < STALE_AFTER


live = LiveState()


# ---------------------------------------------------------------------------
# Fallback simulator (mirrors State_Machine_CANIS.ino) — used only when the
# ESP32 is silent, so the page is never blank during a demo.
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
                          (6, "trigger"), (14, "reset")]  # 20s demo loop

    def _classify(self, a):
        if a >= CORRECTION_ENTER:
            return "CORRECTION"
        if a >= AROUSAL_ENTER:
            return "AROUSAL"
        return "CALM"

    def snapshot(self):
        now = time.time()
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

        new_state = self._classify(self.arousal)
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
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    return jsonify(live.data if live.fresh() else sim.snapshot())


@app.route("/ingest", methods=["POST"])
def ingest():
    payload = request.get_json(silent=True) or {}
    if "state" not in payload:
        return jsonify({"ok": False, "error": "missing 'state'"}), 400
    live.ingest(payload)
    return jsonify({"ok": True})


if __name__ == "__main__":
    import os
    # host=0.0.0.0 so the ESP32 and your phone on the same WiFi can reach it.
    # debug OFF by default (clean + safe for the systemd service); for local
    # dev with auto-reload, run:  CANIS_DEBUG=1 python3 app.py
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("CANIS_DEBUG") == "1")
