"""
test_exposure.py - dashboard two-step exposure arm/confirm [C4]
==============================================================
exposure_arm.ExposureArm is the dashboard mirror of the firmware's 'm'/'y'
defense. It is Flask-free (like event_log.py) so it tests off-server here; the
HTTP wiring is covered in test_dashboard_api.py.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dashboard")))

from exposure_arm import ExposureArm  # noqa: E402


def test_arm_then_confirm_commits():
    e = ExposureArm(confirm_s=5.0)
    ok, _ = e.arm(2, now=0.0)
    assert ok
    assert e.status(0.0)["armed"] is True
    ok, _ = e.confirm(2, now=1.0)
    assert ok
    assert e.authorized_level == 2
    assert e.status(1.0)["armed"] is False


def test_confirm_without_arm_fails():
    e = ExposureArm()
    ok, _ = e.confirm(1, now=0.0)
    assert not ok
    assert e.authorized_level == 0        # nothing changed


def test_confirm_wrong_level_fails_and_clears_arm():
    # A confirm for a level other than the armed one is a failed action, not a
    # silent commit - and it clears the arm so the operator must start over.
    e = ExposureArm()
    e.arm(2, now=0.0)
    ok, _ = e.confirm(1, now=0.1)
    assert not ok
    assert e.authorized_level == 0
    assert e.status(0.2)["armed"] is False


def test_arm_expires_and_cannot_commit_late():
    e = ExposureArm(confirm_s=5.0)
    e.arm(2, now=0.0)
    ok, _ = e.confirm(2, now=6.0)          # past the window
    assert not ok
    assert e.authorized_level == 0


def test_status_expires_stale_arm_on_read():
    e = ExposureArm(confirm_s=2.0)
    e.arm(1, now=0.0)
    assert e.status(1.0)["armed"] is True
    assert e.status(3.0)["armed"] is False  # lazily expired


def test_invalid_level_rejected():
    e = ExposureArm()
    assert e.arm(3, now=0.0)[0] is False
    assert e.arm(-1, now=0.0)[0] is False
    assert e.status(0.0)["armed"] is False


def test_disarm_clears_pending_arm():
    e = ExposureArm()
    e.arm(2, now=0.0)
    e.disarm()
    assert e.status(0.1)["armed"] is False


def test_seconds_left_counts_down():
    e = ExposureArm(confirm_s=5.0)
    e.arm(2, now=0.0)
    assert e.status(0.0)["seconds_left"] == 5.0
    assert e.status(3.0)["seconds_left"] == 2.0
