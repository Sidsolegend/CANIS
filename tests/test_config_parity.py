"""
test_config_parity.py - anti-drift test between canis_config.h and canis_config.py
==================================================================================
The whole point of the single-source pass was that the firmware header and the
Python mirror hold the SAME numbers. A human still has to copy values between
them (a .h cannot be imported by Python), and hand-copies drift - the original
tree drifted exactly this way (CALM_REWARD_MS: 30000 vs 15000).

This test machine-checks the pair: it parses the #define lines out of
canis_config.h and compares each mirrored constant against
dashboard/canis_config.py. Any future edit that updates one file but
not the other now fails CI instead of silently misbehaving on hardware.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import canis_logic  # noqa: F401,E402  (ensures the dashboard path is on sys.path)
import canis_config as cfg  # noqa: E402

_HEADER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "canis_config.h"))

# (header macro, python name)
MIRRORED = [
    ("CANIS_AROUSAL_MAX", "AROUSAL_MAX"),
    ("CANIS_DECAY_PER_SEC", "DECAY_PER_SEC"),
    ("CANIS_TRIGGER_BUMP", "TRIGGER_BUMP"),
    ("CANIS_AROUSAL_RISE_PER_SEC", "AROUSAL_RISE_PER_SEC"),
    ("CANIS_AROUSAL_ENTER", "AROUSAL_ENTER"),
    ("CANIS_AROUSAL_EXIT", "AROUSAL_EXIT"),
    ("CANIS_CORRECTION_ENTER", "CORRECTION_ENTER"),
    ("CANIS_CORRECTION_EXIT", "CORRECTION_EXIT"),
    ("CANIS_DT_MAX_S", "DT_MAX_S"),
    ("CANIS_CALM_REWARD_MS", "CALM_REWARD_MS"),
    ("CANIS_REWARD_COOLDOWN_MS", "REWARD_COOLDOWN_MS"),
    ("CANIS_MAX_SESSION_REWARDS", "MAX_SESSION_REWARDS"),
    ("CANIS_VISION_TTL_MS", "VISION_TTL_MS"),
    ("CANIS_JAM_FAULT_THRESHOLD", "JAM_FAULT_THRESHOLD"),
    ("CANIS_EXPOSURE_DEFAULT", "EXPOSURE_DEFAULT"),
    ("CANIS_EXPOSURE_CONFIRM_MS", "EXPOSURE_CONFIRM_MS"),
    ("CANIS_OBSERVE_ONLY", "OBSERVE_ONLY"),
]

_DEFINE_RE = re.compile(
    r"^\s*#define\s+(CANIS_\w+)\s+([0-9][0-9_.]*)\s*(?:UL|f|F)?\s*(?://.*)?$")


def _parse_header():
    values = {}
    with open(_HEADER, "r", encoding="utf-8") as f:
        for line in f:
            m = _DEFINE_RE.match(line)
            if m:
                values[m.group(1)] = float(m.group(2))
    return values


def test_header_parse_found_all_mirrored_macros():
    hdr = _parse_header()
    missing = [macro for macro, _ in MIRRORED if macro not in hdr]
    assert not missing, f"macros not found/parsed in canis_config.h: {missing}"


def test_python_mirror_matches_header():
    hdr = _parse_header()
    mismatches = []
    for macro, pyname in MIRRORED:
        h = hdr.get(macro)
        p = float(getattr(cfg, pyname))
        if h is not None and abs(h - p) > 1e-9:
            mismatches.append(f"{macro}={h} but canis_config.{pyname}={p}")
    assert not mismatches, "config drift detected:\n  " + "\n  ".join(mismatches)


def test_header_invariants_hold():
    """The header's #error guards, re-checked here for the Python side too."""
    hdr = _parse_header()
    assert hdr["CANIS_CALM_REWARD_MS"] >= hdr["CANIS_REWARD_COOLDOWN_MS"]
    assert hdr["CANIS_CORRECTION_EXIT"] < hdr["CANIS_CORRECTION_ENTER"]
    assert hdr["CANIS_AROUSAL_EXIT"] < hdr["CANIS_AROUSAL_ENTER"]
    assert hdr["CANIS_MOVE_PULSE_MS"] < 1000
    assert hdr["CANIS_VISION_TTL_MS"] > 2 * hdr["CANIS_POLL_MS"]
