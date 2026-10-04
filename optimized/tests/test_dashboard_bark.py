"""
test_dashboard_bark.py — the dashboard side of "treat when he stops barking".

No hardware, no network: Flask's test client in a SUBPROCESS (the dashboard binds its singletons at import),
with its event log pointed at a temp folder so the real study log is never touched.

Asserts:
  * /bark is token-gated, validates 'event', and always answers JSON;
  * a 'quiet' event advances the counter that rides on GET /command as X-CANIS-Quiet-Seq,
    and the /command BODY is unchanged ("NONE" or a zone label);
  * the counter is strictly increasing, even for two 'quiet' events in the same second;
  * a 'quiet' from a dead mic is REFUSED (silence is not a quiet dog), and works again once the mic is back;
  * 'bark' events are counted and logged for the study.

Run:  pytest optimized/tests/test_dashboard_bark.py
"""

import os
import subprocess
import sys
import tempfile

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_SMOKE = r'''
import json, os, sys
repo = os.environ["CANIS_REPO"]
sys.path.insert(0, os.path.join(repo, "dashboard"))
sys.path.insert(0, repo)
import app as dashboard

TOK = {"X-CANIS-Token": "smoke-token"}
c = dashboard.app.test_client()

def post(body, headers=TOK):
    return c.post("/bark", data=json.dumps(body), content_type="application/json", headers=headers)

def seq():
    r = c.get("/command", headers=TOK)
    assert r.status_code == 200
    return int(r.headers["X-CANIS-Quiet-Seq"]), r.get_data(as_text=True)

def age():
    return float(c.get("/command", headers=TOK).headers["X-CANIS-Quiet-Age"])

# token gate + validation
assert post({"event": "quiet"}, headers={}).status_code == 401
assert post({"event": "nonsense"}).status_code == 400
assert post({}).status_code == 400

# /command body unchanged, header present, counter starts at 0
s0, body = seq()
assert s0 == 0 and body == "NONE", (s0, body)
assert age() == -1.0                      # no quiet event yet: age is -1 and the ESP32 never rewards it

# a bark is counted but does not advance the treat counter
r = post({"event": "bark", "duration_s": 0.6, "peak_db": -3.0}).get_json()
assert r["ok"] and r["accepted"] is False
assert seq()[0] == 0
assert c.get("/api/bark").get_json()["barks_total"] == 1

# a quiet advances it, strictly, even twice in the same second
r = post({"event": "quiet", "barks": 3}).get_json()
assert r["ok"] and r["accepted"] is True
s1 = seq()[0]
assert s1 > 0
assert 0.0 <= age() < 3.0                 # a fresh event reports its age in seconds
post({"event": "quiet", "barks": 1})
s2 = seq()[0]
assert s2 > s1, (s1, s2)

# the dashboard body still reports the zone label, header still rides along
c.post("/detect", data=json.dumps({"zone": "kitchen-center", "dog_present": True}),
       content_type="application/json", headers=TOK)
s3, body = seq()
assert body == "kitchen-center" and s3 == s2, (s3, body)

# a dead mic: quiet is refused and the counter does not move
post({"event": "mic_lost"})
r = post({"event": "quiet", "barks": 2}).get_json()
assert r["ok"] and r["accepted"] is False
assert seq()[0] == s2
assert c.get("/api/bark").get_json()["mic_ok"] is False

# mic back: quiet works again
post({"event": "mic_ok"})
assert post({"event": "quiet", "barks": 1}).get_json()["accepted"] is True
assert seq()[0] > s2

# the study log has the events
log = open(os.path.join(os.environ["CANIS_LOG_DIR"], dashboard.cfg.EVENT_LOG_FILE)).read()
for t in ('"type":"bark"', '"type":"bark_quiet"', '"type":"mic_status"'):
    assert t in log, t
print("BARK SMOKE OK")
'''


def test_bark_endpoint_and_command_header():
    env = dict(os.environ)
    env.update({
        "CANIS_REPO": _REPO,
        "CANIS_MOBILE_ENABLED": "0",
        "CANIS_TOKEN": "smoke-token",
        "CANIS_LOG_DIR": tempfile.mkdtemp(prefix="canis-bark-"),
    })
    p = subprocess.run([sys.executable, "-c", _SMOKE], env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, "\n--- stdout ---\n%s\n--- stderr ---\n%s" % (p.stdout, p.stderr)
    assert "BARK SMOKE OK" in p.stdout
