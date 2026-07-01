"""
test_state.py — pure-logic tests for the CANIS arousal state machine.

Covers, with no hardware:
  * Schmitt-trigger hysteresis: enter/exit thresholds do not chatter at a boundary.
  * dt clamp: a stalled loop cannot teleport arousal into CORRECTION in one tick.
  * reward cooldown + session cap.
  * the level-1 "orient only, NEVER approach" safety fix (no FORWARD < level 2).

Run:  pytest optimized/tests/test_state.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import canis_logic as L  # noqa: E402


# --------------------------------------------------------------------------- #
# Hysteresis / state machine
# --------------------------------------------------------------------------- #
def test_thresholds_are_ordered_for_hysteresis():
    assert L.AROUSAL_EXIT < L.AROUSAL_ENTER
    assert L.CORRECTION_EXIT < L.CORRECTION_ENTER
    assert L.AROUSAL_ENTER < L.CORRECTION_ENTER


def test_enter_transitions_from_calm():
    assert L.classify("CALM", 0.0) == "CALM"
    assert L.classify("CALM", L.AROUSAL_ENTER) == "AROUSAL"
    assert L.classify("CALM", L.CORRECTION_ENTER) == "CORRECTION"


def test_deadband_does_not_chatter_at_correction_boundary():
    # Sitting just below ENTER but above EXIT: once in CORRECTION we stay there;
    # from AROUSAL we do NOT spuriously enter CORRECTION. No per-frame flapping.
    a = (L.CORRECTION_ENTER + L.CORRECTION_EXIT) / 2.0
    assert L.classify("CORRECTION", a) == "CORRECTION"
    assert L.classify("AROUSAL", a) == "AROUSAL"


def test_deadband_does_not_chatter_at_arousal_boundary():
    a = (L.AROUSAL_ENTER + L.AROUSAL_EXIT) / 2.0
    assert L.classify("AROUSAL", a) == "AROUSAL"
    assert L.classify("CALM", a) == "CALM"


def test_correction_eases_to_arousal_then_calm():
    # Must fall below CORRECTION_EXIT to drop to AROUSAL, and below AROUSAL_EXIT
    # to drop all the way to CALM.
    assert L.classify("CORRECTION", L.CORRECTION_EXIT - 0.1) == "AROUSAL"
    assert L.classify("CORRECTION", L.AROUSAL_EXIT - 0.1) == "CALM"


def test_no_chatter_over_a_hovering_sweep():
    # Simulate arousal hovering right around the correction boundary; a correct
    # Schmitt trigger produces at most one enter and one exit, never a storm.
    state = "AROUSAL"
    transitions = 0
    # climb just over ENTER, then hover in the dead-band, then ease out.
    seq = ([L.CORRECTION_ENTER + 1] +
           [(L.CORRECTION_ENTER + L.CORRECTION_EXIT) / 2.0] * 20 +
           [L.CORRECTION_EXIT - 1] * 5)
    for a in seq:
        nxt = L.classify(state, a)
        if nxt != state:
            transitions += 1
        state = nxt
    assert transitions == 2  # one enter (CORRECTION), one exit (AROUSAL)


# --------------------------------------------------------------------------- #
# dt clamp (OPT-4)
# --------------------------------------------------------------------------- #
def test_dt_clamp_prevents_single_tick_jump_to_correction():
    # A 5-second stall with detection active. Unclamped that would add
    # (RISE)*5 = 150 -> instant CORRECTION. Clamped, one tick can add at most
    # RISE*DT_MAX = 6, so from 0 we stay far below CORRECTION_ENTER.
    a = L.update_arousal(0.0, dt=5.0, detecting=True)
    assert a < L.CORRECTION_ENTER
    assert a == L.AROUSAL_RISE_PER_SEC * L.DT_MAX_S - L.DECAY_PER_SEC * L.DT_MAX_S


def test_arousal_decays_and_clamps_to_zero():
    a = L.update_arousal(2.0, dt=1.0, detecting=False)
    assert a == 0.0  # decay would go negative -> clamped at 0


def test_arousal_clamped_to_max():
    a = L.update_arousal(L.AROUSAL_MAX, dt=L.DT_MAX_S, detecting=True)
    assert a == L.AROUSAL_MAX


# --------------------------------------------------------------------------- #
# Reward cooldown + session cap
# --------------------------------------------------------------------------- #
def test_reward_cooldown_blocks_back_to_back():
    r = L.RewardController()
    assert r.request(1000) is True                      # first treat
    assert r.request(1000 + 1) is False                 # inside cooldown
    assert r.request(1000 + L.REWARD_COOLDOWN_MS - 1) is False
    assert r.request(1000 + L.REWARD_COOLDOWN_MS) is True  # cooldown elapsed
    assert r.reward_count == 2


def test_reward_session_cap():
    r = L.RewardController()
    now = 0
    for _ in range(L.MAX_SESSION_REWARDS):
        assert r.request(now) is True
        now += L.REWARD_COOLDOWN_MS      # always past cooldown
    assert r.reward_count == L.MAX_SESSION_REWARDS
    now += L.REWARD_COOLDOWN_MS
    assert r.request(now) is False       # capped
    assert r.reward_count == L.MAX_SESSION_REWARDS


# --------------------------------------------------------------------------- #
# Level-1 "orient only, NEVER approach" safety fix
# --------------------------------------------------------------------------- #
def test_level1_forward_is_rewritten_to_pivot():
    # The core safety invariant: below level 2, a FORWARD bearing must never be
    # driven forward (would approach the dog); it becomes an in-place pivot.
    assert L.safe_orient_dir("FORWARD", 0) != "FORWARD"
    assert L.safe_orient_dir("FORWARD", 1) != "FORWARD"
    assert L.safe_orient_dir("FORWARD", 1) == "LEFT"


def test_level1_non_forward_bearings_unchanged():
    for d in ("LEFT", "RIGHT"):
        assert L.safe_orient_dir(d, 1) == d


def test_level2_allows_forward_approach():
    # Level >= 2 behaviour is unchanged: approach is permitted.
    assert L.safe_orient_dir("FORWARD", 2) == "FORWARD"
    assert L.safe_orient_dir("LEFT", 2) == "LEFT"
