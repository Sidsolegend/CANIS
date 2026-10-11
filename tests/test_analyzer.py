"""
test_analyzer.py - study/analyze_sessions.py end-to-end
=======================================================
The analyzer must (a) generate a clearly-synthetic example log, (b) aggregate
it through the SAME summarize_sessions used by the dashboard export, and
(c) run as a CLI. No real data is involved anywhere here.
"""

import os
import subprocess
import sys

_STUDY = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "study"))
sys.path.insert(0, _STUDY)
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dashboard")))

import analyze_sessions  # noqa: E402
import event_log as elog  # noqa: E402


def test_generate_example_is_marked_synthetic(tmp_path):
    path = str(tmp_path / "ex.jsonl")
    analyze_sessions.generate_example(path)
    events = elog.read_events(path)
    assert events, "example generator wrote nothing"
    assert events[0].get("synthetic") is True
    assert "SYNTHETIC" in events[0].get("note", "")
    assert all("SYNTHETIC" in e["session_id"] for e in events)


def test_example_aggregates_sanely(tmp_path):
    path = str(tmp_path / "ex.jsonl")
    analyze_sessions.generate_example(path)
    summaries = elog.summarize_sessions(elog.read_events(path), zone_gap_s=3.0)
    assert len(summaries) == 2
    a, b = summaries
    # session A: 2 zone episodes, 3 treats, 1 correction, no jams
    assert a["zone_episodes"] == 2
    assert a["treats_dispensed"] == 3
    assert a["corrections_entered"] == 1
    assert a["jams"] == 0
    # session B: exposure level 1 used, 1 jam, 1 link gap
    assert b["exposure_levels"] == "1"
    assert b["jams"] == 1
    assert b["link_gaps"] == 1
    # state occupancy must sum to <= session duration (never fabricate time)
    for s in summaries:
        occupied = s["time_calm_s"] + s["time_arousal_s"] + s["time_correction_s"]
        assert occupied <= s["duration_s"] + 0.001


def test_cli_runs_and_prints_synthetic_banner(tmp_path):
    path = str(tmp_path / "ex.jsonl")
    out_csv = str(tmp_path / "summary.csv")
    script = os.path.join(_STUDY, "analyze_sessions.py")
    proc = subprocess.run(
        [sys.executable, script, "--generate-example", path, "--csv", out_csv],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "SYNTHETIC EXAMPLE DATA" in proc.stdout
    assert "session_id" in proc.stdout
    # CSV written with the stable schema header
    header = open(out_csv).readline().strip().split(",")
    assert header == elog.SESSION_CSV_FIELDS
