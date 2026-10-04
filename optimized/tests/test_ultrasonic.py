"""
test_ultrasonic.py — lock-up handling for the HC-SR04 / SR04M-2 ultrasonics, no hardware.

A hung sensor reads as 'no data', which the roam logic used to treat as 'path clear'. These tests pin
the safe behaviour: it is detected, marked FAULTED, treated as BLOCKED (never drive on blind), and re-tried.

Run:  pytest optimized/tests/test_ultrasonic.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import canis_logic as L  # noqa: E402


def _stuck():
    return L.US_HUNG


def test_not_faulted_until_enough_consecutive_stuck_reads():
    ch = L.UltrasonicChannel()
    for i in range(L.US_HUNG_RUNS - 1):
        assert ch.poll(i * 100, _stuck) == -1.0
        assert not ch.fault
    ch.poll(L.US_HUNG_RUNS * 100, _stuck)
    assert ch.fault


def test_a_good_read_resets_the_count():
    ch = L.UltrasonicChannel()
    for i in range(L.US_HUNG_RUNS - 1):
        ch.poll(i * 100, _stuck)
    assert ch.poll(1000, lambda: 42.0) == 42.0
    for i in range(L.US_HUNG_RUNS - 1):
        ch.poll(2000 + i * 100, _stuck)
    assert not ch.fault                       # the earlier near-miss did not accumulate


def test_no_echo_is_not_a_fault():
    ch = L.UltrasonicChannel()
    for i in range(50):
        assert ch.poll(i * 100, lambda: -1.0) == -1.0
    assert not ch.fault                       # open air legitimately returns no echo


def test_faulted_sensor_is_parked_until_the_retry_window():
    ch = L.UltrasonicChannel()
    for i in range(L.US_HUNG_RUNS):
        ch.poll(i * 100, _stuck)
    t_fault = ch.fault_since
    calls = []

    def read():
        calls.append(1)
        return 30.0

    assert ch.poll(t_fault + L.US_FAULT_RETRY_MS - 1, read) == -1.0
    assert calls == []                         # not read while parked
    assert ch.fault


def test_recovers_after_the_retry_when_it_answers():
    ch = L.UltrasonicChannel()
    for i in range(L.US_HUNG_RUNS):
        ch.poll(i * 100, _stuck)
    assert ch.poll(ch.fault_since + L.US_FAULT_RETRY_MS, lambda: 55.0) == 55.0
    assert not ch.fault and ch.hung_runs == 0


def test_still_stuck_at_the_retry_stays_faulted_and_waits_again():
    ch = L.UltrasonicChannel()
    for i in range(L.US_HUNG_RUNS):
        ch.poll(i * 100, _stuck)
    t = ch.fault_since + L.US_FAULT_RETRY_MS
    ch.poll(t, _stuck)
    assert ch.fault and ch.fault_since == t


# ---- roam: a hung sensor is a blind spot, never 'clear' --------------------
def test_roam_goes_forward_when_everything_is_clear():
    assert L.roam_decision(-1, -1, -1) == "FORWARD"


def test_roam_never_goes_forward_with_a_hung_centre():
    assert L.roam_decision(-1, -1, -1, faults=(False, True, False)) != "FORWARD"


def test_roam_never_goes_forward_with_any_hung_sensor():
    for k in range(3):
        f = [False, False, False]
        f[k] = True
        assert L.roam_decision(-1, -1, -1, faults=tuple(f)) != "FORWARD"


def test_a_hung_side_counts_as_no_room():
    # right is hung, left is blocked close: still prefer LEFT over driving into the hung side
    assert L.roam_decision(20.0, -1, -1, faults=(False, False, True)) == "LEFT"
    # left hung, right clear: turn RIGHT
    assert L.roam_decision(-1, 20.0, -1, faults=(True, False, False)) == "RIGHT"


def test_roam_steers_toward_the_roomier_side_when_blocked():
    assert L.roam_decision(100.0, 20.0, 50.0) == "LEFT"
    assert L.roam_decision(50.0, 20.0, 100.0) == "RIGHT"
