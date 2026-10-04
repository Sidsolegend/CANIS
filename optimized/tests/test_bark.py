"""
test_bark.py — the bark / "went quiet" decision logic, no audio and no hardware.

The treat is dropped when Shifu has been quiet after barking, so the dangerous failures are:
a treat while there is still noise, a treat for nothing, and a treat because the mic died.

Run:  pytest optimized/tests/test_bark.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "audio"))

import bark_logic as B  # noqa: E402

ROOM = -58.0   # quiet room (dBFS), from the real recording
BARK = -5.0    # a close bark, from the real recording
TALK = -35.0   # speech / footsteps in the same recording


def run(det, segments, t0=0.0):
    """segments: list of (level_db, seconds). Returns (events, end_time)."""
    t = t0
    out = []
    for level, secs in segments:
        for _ in range(int(round(secs / B.FRAME_S))):
            out.extend(det.feed(level, t))
            t += B.FRAME_S
    return out, t


def kinds(events):
    return [e[0] for e in events]


def test_nothing_happens_in_a_quiet_room():
    ev, _ = run(B.BarkDetector(), [(ROOM, 60)])
    assert ev == []


def test_one_bark_then_quiet_gives_a_bark_and_one_quiet():
    ev, _ = run(B.BarkDetector(), [(ROOM, 3), (BARK, 0.5), (ROOM, 8)])
    assert kinds(ev) == ["bark", "quiet"]


def test_quiet_arrives_about_quiet_s_after_the_bark_ends():
    det = B.BarkDetector(quiet_s=5.0)
    ev, _ = run(det, [(ROOM, 3), (BARK, 0.5), (ROOM, 10)])
    t_bark_end = next(e[1] for e in ev if e[0] == "bark")
    t_quiet = next(e[1] for e in ev if e[0] == "quiet")
    assert 4.9 <= t_quiet - t_bark_end <= 5.6


def test_no_quiet_event_before_the_quiet_time():
    ev, _ = run(B.BarkDetector(quiet_s=5.0), [(ROOM, 3), (BARK, 0.5), (ROOM, 4)])
    assert "quiet" not in kinds(ev)


def test_a_run_of_barks_is_one_episode_and_one_quiet():
    ev, _ = run(B.BarkDetector(), [(ROOM, 3), (BARK, 0.4), (ROOM, 1.0), (BARK, 0.5), (ROOM, 1.5),
                                   (BARK, 0.4), (ROOM, 9)])
    assert kinds(ev).count("bark") == 3
    assert kinds(ev).count("quiet") == 1
    assert [e for e in ev if e[0] == "quiet"][0][2] == 3          # reports 3 barks


def test_quiet_fires_once_not_every_frame():
    ev, _ = run(B.BarkDetector(), [(ROOM, 3), (BARK, 0.5), (ROOM, 40)])
    assert kinds(ev).count("quiet") == 1


def test_a_second_episode_gets_its_own_quiet():
    ev, _ = run(B.BarkDetector(), [(ROOM, 3), (BARK, 0.5), (ROOM, 8), (BARK, 0.5), (ROOM, 8)])
    assert kinds(ev) == ["bark", "quiet", "bark", "quiet"]


def test_a_click_is_not_a_bark_and_gives_no_quiet():
    ev, _ = run(B.BarkDetector(), [(ROOM, 3), (BARK, 0.05), (ROOM, 20)])
    assert ev == []


def test_talking_level_noise_is_ignored():
    ev, _ = run(B.BarkDetector(), [(ROOM, 3), (TALK, 2.0), (ROOM, 20)])
    assert ev == []


def test_a_long_loud_noise_is_not_a_bark():
    ev, _ = run(B.BarkDetector(), [(ROOM, 3), (BARK, 6.0), (ROOM, 20)])
    assert ev == []                                  # a TV / howl / vacuum, not a bark


def test_loud_noise_after_a_bark_holds_back_the_treat():
    det = B.BarkDetector(quiet_s=5.0)
    ev, t = run(det, [(ROOM, 3), (BARK, 0.5), (ROOM, 3), (BARK, 8.0)])   # bark, then a long racket
    assert "quiet" not in kinds(ev)
    ev2, _ = run(det, [(ROOM, 10)], t0=t)            # it only counts once the racket has stopped
    assert kinds(ev2) == ["quiet"]


def test_losing_the_mic_cancels_the_pending_quiet():
    det = B.BarkDetector()
    ev, t = run(det, [(ROOM, 3), (BARK, 0.5), (ROOM, 2)])
    assert kinds(ev) == ["bark"]
    det.reset_episode()                              # what the caller does when the mic drops
    ev2, _ = run(det, [(B.DB_MIN, 20)], t0=t)
    assert "quiet" not in kinds(ev2)


def test_digital_silence_at_startup_does_not_break_the_floor():
    ev, _ = run(B.BarkDetector(), [(-180.0, 0.5), (ROOM, 3), (BARK, 0.5), (ROOM, 8)])
    assert kinds(ev) == ["bark", "quiet"]


def test_a_slowly_rising_room_does_not_trigger_barks():
    det = B.BarkDetector()
    segs = [(ROOM + i * 0.5, 2.0) for i in range(0, 24)]      # +12 dB over ~48 s
    ev, _ = run(det, [(ROOM, 3)] + segs)
    assert ev == []


def test_no_decisions_while_it_is_still_listening_for_the_floor():
    ev, _ = run(B.BarkDetector(), [(BARK, 0.5)])               # loud before the floor is known
    assert ev == []


def test_digital_silence_after_a_bark_is_never_quiet():
    # a dead / switched-off transmitter streaming zeros must NOT earn a treat, however long it lasts
    ev, _ = run(B.BarkDetector(), [(ROOM, 3), (BARK, 0.5), (-180.0, 60)])
    assert kinds(ev) == ["bark"]


def test_quiet_is_counted_from_when_real_audio_returns():
    det = B.BarkDetector(quiet_s=5.0)
    ev, t = run(det, [(ROOM, 3), (BARK, 0.5), (-180.0, 4)])           # mic dead for 4 s right after the bark
    assert kinds(ev) == ["bark"]
    ev2, _ = run(det, [(ROOM, 8)], t0=t)                              # live audio again
    assert kinds(ev2) == ["quiet"]
    assert ev2[0][1] - t >= 4.9                                       # a full 5 s of REAL quiet, not the dead time


def test_the_mic_lost_timeout_is_shorter_than_the_quiet_time():
    import detect_bark as D
    assert D.DEAD_S < B.QUIET_S
