"""
test_event_log.py - JSONL event log + session aggregation [OPT-14]
==================================================================
Covers the dashboard's study instrumentation: schema of written events,
crash-tolerant reading, and the per-session metrics used by both
/export/sessions.csv and study/analyze_sessions.py.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dashboard")))

import event_log as elog  # noqa: E402


# --------------------------------------------------------------------------- #
# EventLog writer
# --------------------------------------------------------------------------- #
def test_log_writes_one_json_line_with_schema(tmp_path):
    path = str(tmp_path / "events.jsonl")
    log = elog.EventLog(path)
    evt = log.log("state_transition", **{"from": "CALM", "to": "AROUSAL", "arousal": 21.0})

    lines = open(path).read().splitlines()
    assert len(lines) == 1
    on_disk = json.loads(lines[0])
    assert on_disk == evt
    for field in ("ts", "iso", "session_id", "type"):
        assert field in on_disk
    assert on_disk["type"] == "state_transition"
    assert on_disk["from"] == "CALM" and on_disk["to"] == "AROUSAL"


def test_log_appends_and_shares_session_id(tmp_path):
    path = str(tmp_path / "events.jsonl")
    log = elog.EventLog(path)
    log.log("session_start")
    log.log("treat_dispensed", reward_count=1)
    evts = elog.read_events(path)
    assert [e["type"] for e in evts] == ["session_start", "treat_dispensed"]
    assert evts[0]["session_id"] == evts[1]["session_id"]


def test_read_events_survives_truncated_last_line(tmp_path):
    # A power cut mid-write must not lose the whole file.
    path = str(tmp_path / "events.jsonl")
    log = elog.EventLog(path)
    log.log("session_start")
    with open(path, "a") as f:
        f.write('{"ts": 1, "truncat')   # simulated torn write
    evts = elog.read_events(path)
    assert len(evts) == 1
    assert evts[0]["type"] == "session_start"


def test_creates_missing_log_directory(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "events.jsonl")
    elog.EventLog(path).log("session_start")
    assert os.path.exists(path)


# --------------------------------------------------------------------------- #
# summarize_sessions
# --------------------------------------------------------------------------- #
def _evt(ts, etype, session="S", **fields):
    e = {"ts": ts, "iso": "", "session_id": session, "type": etype}
    e.update(fields)
    return e


def test_summary_counts_and_state_occupancy():
    events = [
        _evt(0.0, "session_start"),
        _evt(10.0, "state_transition", **{"from": "CALM", "to": "AROUSAL", "arousal": 21.0}),
        _evt(14.0, "state_transition", **{"from": "AROUSAL", "to": "CORRECTION", "arousal": 70.5}),
        _evt(20.0, "state_transition", **{"from": "CORRECTION", "to": "CALM", "arousal": 11.0}),
        _evt(21.0, "treat_dispensed", reward_count=1),
        _evt(30.0, "dispenser_jam", jam_count=1),
        _evt(40.0, "link_gap", gap_sec=7.5),
    ]
    (s,) = elog.summarize_sessions(events)
    assert s["transitions"] == 3
    assert s["corrections_entered"] == 1
    assert s["treats_dispensed"] == 1
    assert s["jams"] == 1
    assert s["link_gaps"] == 1
    # occupancy: CALM 0-10 and 20-40 = 30 s; AROUSAL 10-14 = 4 s; CORR 14-20 = 6 s
    assert s["time_calm_s"] == 30.0
    assert s["time_arousal_s"] == 4.0
    assert s["time_correction_s"] == 6.0
    assert s["duration_s"] == 40.0


def test_zone_episodes_cluster_by_gap():
    # 1 Hz sightings 0-5 s, then a 10 s gap, then 15-16 s: two episodes.
    events = [_evt(float(t), "zone_seen", zone="k") for t in range(6)]
    events += [_evt(15.0, "zone_seen", zone="k"), _evt(16.0, "zone_seen", zone="k")]
    (s,) = elog.summarize_sessions(events, zone_gap_s=3.0)
    assert s["zone_episodes"] == 2
    assert s["zone_time_s"] == 5.0 + 1.0
    assert s["first_zone_latency_s"] == 0.0


def test_sessions_are_separated_and_ordered():
    events = [
        _evt(100.0, "session_start", session="B"),
        _evt(101.0, "treat_dispensed", session="B", reward_count=1),
        _evt(0.0, "session_start", session="A"),
    ]
    a, b = elog.summarize_sessions(events)
    assert a["session_id"] == "A" and b["session_id"] == "B"
    assert b["treats_dispensed"] == 1


def test_unknown_event_types_are_tolerated():
    events = [_evt(0.0, "some_future_event"), _evt(1.0, "treat_dispensed", reward_count=1)]
    (s,) = elog.summarize_sessions(events)
    assert s["treats_dispensed"] == 1


def test_exposure_levels_recorded():
    events = [
        _evt(0.0, "session_start"),
        _evt(1.0, "exposure_change", **{"from": 0, "to": 1}),
        _evt(2.0, "exposure_change", **{"from": 1, "to": 2}),
    ]
    (s,) = elog.summarize_sessions(events)
    assert s["exposure_levels"] == "1,2"
