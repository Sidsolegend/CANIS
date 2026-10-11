"""
test_vision_logic.py - marker-loss grace period + frozen-frame guard [OPT-12/13]
================================================================================
vision_logic.py is deliberately cv2-free so these tests run on any machine
(including one with no OpenCV): the camera/ArUco plumbing stays in detect.py,
the decisions are tested here.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "vision")))

from vision_logic import DetectionGrace, FrozenFrameGuard, BlurSkipMonitor  # noqa: E402


# --------------------------------------------------------------------------- #
# DetectionGrace [OPT-12]
# --------------------------------------------------------------------------- #
def test_sighting_reports_its_zone():
    g = DetectionGrace(grace_s=1.5)
    assert g.update(True, "kitchen-left", now=10.0) == "kitchen-left"


def test_brief_loss_is_bridged_by_grace():
    # Dog turns his head for a second: the marker vanishes but the detection
    # must NOT flap (a flap saw-tooths arousal and can fire a false
    # leaving-reward at a dog that never left).
    g = DetectionGrace(grace_s=1.5)
    g.update(True, "kitchen-left", now=10.0)
    assert g.update(False, None, now=10.5) == "kitchen-left"   # held
    assert g.update(False, None, now=11.5) == "kitchen-left"   # still held (== grace)
    assert g.holding


def test_real_departure_clears_after_grace():
    g = DetectionGrace(grace_s=1.5)
    g.update(True, "kitchen-left", now=10.0)
    assert g.update(False, None, now=11.6) is None   # past the grace window
    assert not g.holding


def test_new_sighting_updates_zone_and_restarts_grace():
    g = DetectionGrace(grace_s=1.5)
    g.update(True, "kitchen-left", now=10.0)
    g.update(True, "kitchen-right", now=12.0)        # dog moved across frame
    assert g.update(False, None, now=13.0) == "kitchen-right"


def test_no_sighting_ever_reports_none():
    g = DetectionGrace(grace_s=1.5)
    assert g.update(False, None, now=1.0) is None
    assert g.update(False, None, now=2.0) is None


def test_grace_does_not_resurrect_after_clear():
    # Once cleared, an old zone must never come back without a real sighting.
    g = DetectionGrace(grace_s=1.0)
    g.update(True, "kitchen-left", now=10.0)
    assert g.update(False, None, now=12.0) is None
    assert g.update(False, None, now=12.1) is None


# --------------------------------------------------------------------------- #
# FrozenFrameGuard [OPT-13]
# --------------------------------------------------------------------------- #
def test_live_video_never_trips_guard():
    fg = FrozenFrameGuard(max_identical=5)
    for i in range(100):
        assert fg.update(f"sig-{i}") is False   # every frame differs (sensor noise)


def test_frozen_stream_trips_after_threshold():
    fg = FrozenFrameGuard(max_identical=5)
    results = [fg.update("same") for _ in range(7)]
    # first frame sets the signature; streak reaches 5 on the 6th identical frame
    assert results[:5] == [False] * 5
    assert results[5] is True


def test_single_changed_frame_resets_streak():
    fg = FrozenFrameGuard(max_identical=3)
    for _ in range(3):
        fg.update("same")
    assert fg.update("different") is False
    assert fg.update("different") is False   # streak restarted


def test_reset_after_reopen():
    fg = FrozenFrameGuard(max_identical=2)
    fg.update("same"); fg.update("same")
    assert fg.update("same") is True
    fg.reset()
    assert fg.update("same") is False        # fresh start after camera reopen


# --------------------------------------------------------------------------- #
# BlurSkipMonitor [C6]
# --------------------------------------------------------------------------- #
def test_blur_monitor_alerts_when_mostly_skipped():
    # Dusk: every frame fails the sharpness gate -> the guard is blacking out
    # detection. That must raise a (single) alert once past min_samples.
    m = BlurSkipMonitor(window_s=60.0, alert_ratio=0.9, min_samples=10)
    fired = [m.record(skipped=True, now=float(i)) for i in range(30)]
    hits = [a for a in fired if a is not None]
    assert len(hits) == 1
    assert hits[0]["ratio"] == 1.0
    assert hits[0]["total"] >= 10


def test_blur_monitor_quiet_on_sharp_video():
    m = BlurSkipMonitor(window_s=60.0, alert_ratio=0.9, min_samples=10)
    for i in range(60):
        assert m.record(skipped=False, now=float(i)) is None
    assert m.ratio(60.0) == 0.0


def test_blur_monitor_needs_min_samples():
    # A handful of blurry frames at startup is 100% but not yet meaningful.
    m = BlurSkipMonitor(window_s=60.0, alert_ratio=0.9, min_samples=20)
    for i in range(5):
        assert m.record(skipped=True, now=float(i)) is None


def test_blur_monitor_rate_limited_to_one_per_window():
    m = BlurSkipMonitor(window_s=60.0, alert_ratio=0.9, min_samples=10)
    alerts = [m.record(skipped=True, now=float(i)) for i in range(40)]
    assert len([a for a in alerts if a is not None]) == 1   # one alert, not 30


def test_blur_monitor_window_prunes_old_skips():
    # Old blurry frames age out of the window; once recent frames are sharp the
    # ratio must fall back to 0 (light returned).
    m = BlurSkipMonitor(window_s=10.0, alert_ratio=0.9, min_samples=5)
    for i in range(10):
        m.record(skipped=True, now=float(i))       # t=0..9 all blurry
    for i in range(10, 30):
        m.record(skipped=False, now=float(i))      # t=10..29 all sharp
    assert m.ratio(30.0) == 0.0                     # only the sharp tail remains
