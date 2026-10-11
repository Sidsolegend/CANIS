"""
test_dashboard_motion.py - [MOBILE] dashboard <-> CBF controller smoke check
=============================================================================
No hardware, no network, no real sockets: Flask's test client only. Each
scenario runs in a SUBPROCESS because both dashboard/app.py (module-level
singletons + tick thread) and control/config.py (CANIS_MOBILE_ENABLED env
mirror) bind their state at import - a fresh interpreter per env is the only
honest way to test both modes.

Asserts, per INTERFACE_CONTRACT.md:
  * GET /motion is token-gated exactly like /command (401 without token);
  * the body is ALWAYS one well-formed §2.1 line: "STOP" or "MOVE %.3f %.3f";
  * a cold dashboard (no ESP32 telemetry ever) serves "STOP" and never errors;
  * POST /telemetry accepts a full §1.2 frame, a partial frame, and garbage
    fields without a 400 (all-optional, bounded);
  * STATIONARY INTEGRITY: with CANIS_MOBILE_ENABLED=0, /motion stays "STOP"
    after telemetry + detection + state feeds and ticker time, tick() returns
    None, and the legacy routes (/api/state, /command) behave as before.

Run:  pytest tests/test_dashboard_motion.py
"""

import os
import subprocess
import sys
import tempfile

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_SMOKE = r'''
import json, os, re, sys, time

repo = os.environ["CANIS_REPO"]
sys.path.insert(0, os.path.join(repo, "dashboard"))
sys.path.insert(0, repo)

import app as dashboard  # noqa: E402  (dashboard/app.py; starts the tick thread)

mobile = os.environ.get("CANIS_MOBILE_ENABLED") == "1"
TOK = {"X-CANIS-Token": os.environ.get("CANIS_TOKEN", "")}
LINE = re.compile(r"^(STOP|MOVE -?\d+\.\d{3} -?\d+\.\d{3})$")
client = dashboard.app.test_client()

def motion_line():
    r = client.get("/motion", headers=TOK)
    assert r.status_code == 200, r.status_code
    assert r.mimetype == "text/plain", r.mimetype
    body = r.get_data(as_text=True)
    assert LINE.match(body), "malformed /motion body: %r" % body
    return body

# The controller must have been instantiated and its tick thread started.
assert dashboard.controller is not None, "MotionController failed to import"
assert dashboard._tick_thread.is_alive(), "tick thread not running"
assert dashboard.controller.mobile is mobile, "env mirror not honored"

# 1) Cold start: no ESP32 telemetry ever -> "STOP", no error.
assert motion_line() == "STOP"

# 2) Token posture identical to /command: no token -> 401 text.
r = client.get("/motion")
assert r.status_code == 401, r.status_code
assert client.get("/command").status_code == 401  # unchanged legacy behavior

# 3) Synthetic feeds through the REAL routes.
r = client.post("/ingest", json={"state": "CALM", "arousal": 5.0,
                                 "arousal_max": 100.0, "exposure_level": 2},
                headers=TOK)
assert r.status_code == 200, r.get_data(as_text=True)
for i in range(6):
    r = client.post("/telemetry", json={
        "ts_ms": 1000 + i * 100, "enc_l": i * 5, "enc_r": i * 5,
        "odom_v": 0.0, "odom_omega": 0.0, "tof_m": 2.0,
        "heading_deg": 0.0}, headers=TOK)
    assert r.status_code == 200, r.get_data(as_text=True)
r = client.post("/detect", json={"zone": "sofa", "dog_present": True,
                                 "bearing_deg": 0.0, "distance_m": 2.0,
                                 "confidence": 0.9}, headers=TOK)
assert r.status_code == 200, r.get_data(as_text=True)

# 4) Give the background ticker a few CONTROL_RATE_HZ periods, then read.
time.sleep(4.0 / dashboard.CONTROL_RATE_HZ + 0.1)
body = motion_line()   # well-formed either way ("MOVE ..." or a hold "STOP")

if not mobile:
    # STATIONARY INTEGRITY: never anything but STOP, and tick() returns None
    # without touching the sink (motion stays at its power-on state).
    assert body == "STOP", "stationary served %r" % body
    assert dashboard.controller.tick() is None
    assert motion_line() == "STOP"
    assert dashboard.motion.at == 0.0, "sink was touched in stationary mode"
else:
    # END-TO-END: dog off-axis + fresh telemetry + exposure 2 must yield an
    # actual barrier-filtered MOVE through the real routes -> controller ->
    # sink -> /motion (proves the wiring delivers commands, not just STOP).
    r = client.post("/detect", json={"zone": "sofa", "dog_present": True,
                                     "bearing_deg": 60.0, "distance_m": 2.0,
                                     "confidence": 0.9}, headers=TOK)
    assert r.status_code == 200
    for i in range(3):
        r = client.post("/telemetry", json={
            "ts_ms": 1700 + i * 100, "enc_l": 25, "enc_r": 25,
            "odom_v": 0.0, "odom_omega": 0.0, "tof_m": 2.0,
            "heading_deg": 0.0}, headers=TOK)
        assert r.status_code == 200
    time.sleep(4.0 / dashboard.CONTROL_RATE_HZ)
    body = motion_line()
    assert body.startswith("MOVE "), "expected a MOVE, got %r" % body

# 5) Robustness: partial and garbage telemetry are accepted, never a 400,
#    and /motion stays well-formed afterward.
for bad in ({}, {"tof_m": "garbage"}, {"ts_ms": None, "odom_v": "x"},
            {"heading_deg": 725.0, "enc_l": 2**80}):
    r = client.post("/telemetry", json=bad, headers=TOK)
    assert r.status_code == 200, (bad, r.get_data(as_text=True))
motion_line()

# 6) Legacy surface unchanged in effect.
snap = json.loads(client.get("/api/state").get_data(as_text=True))
assert snap["state"] in ("CALM", "AROUSAL", "CORRECTION")
r = client.get("/command", headers=TOK)
assert r.status_code == 200 and r.get_data(as_text=True) == "sofa"
r = client.post("/config", json={"mobile_enabled": 0, "fw": "smoke"},
                headers=TOK)
assert r.status_code == 200
assert dashboard.controller.mobile is False  # announce degrades toward off
time.sleep(2.0 / dashboard.CONTROL_RATE_HZ + 0.05)
# after the one-way degrade, ticks stop depositing and TTL expiry => STOP
time.sleep(dashboard.MotionCommand.TTL_S + 0.05)
assert motion_line() == "STOP"

print("SMOKE OK (%s)" % ("mobile" if mobile else "stationary"))
'''


def _run_smoke(mobile):
    env = dict(os.environ)
    env.update({
        "CANIS_REPO": _REPO,
        "CANIS_MOBILE_ENABLED": "1" if mobile else "0",
        "CANIS_TOKEN": "smoke-token",                       # exercise [OPT-2] auth
        "CANIS_LOG_DIR": tempfile.mkdtemp(prefix="canis-smoke-"),
    })
    p = subprocess.run([sys.executable, "-c", _SMOKE], env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, "\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        p.stdout, p.stderr)
    assert "SMOKE OK" in p.stdout


def test_motion_smoke_stationary():
    """CANIS_MOBILE_ENABLED=0: /motion exists, is authed, and STAYS 'STOP'."""
    _run_smoke(mobile=False)


def test_motion_smoke_mobile():
    """CANIS_MOBILE_ENABLED=1: full loop feeds; /motion always well-formed."""
    _run_smoke(mobile=True)
