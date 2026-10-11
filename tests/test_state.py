"""
test_state.py - pure-logic tests for the CANIS arousal state machine.

Covers, with no hardware:
  * Schmitt-trigger hysteresis: enter/exit thresholds do not chatter at a boundary.
  * dt clamp: a stalled loop cannot teleport arousal into CORRECTION in one tick.
  * reward cooldown + session cap.
  * the level-1 "orient only, NEVER approach" safety fix (no FORWARD < level 2).

Run:  pytest tests/test_state.py
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


# --------------------------------------------------------------------------- #
# Exact-boundary semantics (>= enter, strictly < exit) - pin them down so a
# future >= / > slip is caught.
# --------------------------------------------------------------------------- #
def test_exactly_at_enter_threshold_enters():
    assert L.classify("AROUSAL", L.CORRECTION_ENTER) == "CORRECTION"
    assert L.classify("CALM", L.AROUSAL_ENTER) == "AROUSAL"


def test_exactly_at_exit_threshold_does_not_exit():
    # exit requires strictly BELOW the exit level
    assert L.classify("CORRECTION", L.CORRECTION_EXIT) == "CORRECTION"
    assert L.classify("AROUSAL", L.AROUSAL_EXIT) == "AROUSAL"
    # ...but from CORRECTION, sitting AT AROUSAL_EXIT is already far below
    # CORRECTION_EXIT, so it correctly eases down one band (not all the way out).
    assert L.classify("CORRECTION", L.AROUSAL_EXIT) == "AROUSAL"


# --------------------------------------------------------------------------- #
# Closed-loop timing - document (and pin) how long the integrator takes to
# escalate and to release. These numbers matter behaviorally: the release time
# IS the reward latency after the dog leaves (see STUDY_PROTOCOL.md).
# --------------------------------------------------------------------------- #
def _run_loop(arousal, seconds, detecting, tick=0.05):
    state = "CALM"
    transitions = []
    t = 0.0
    while t < seconds:
        arousal = L.update_arousal(arousal, tick, detecting)
        nxt = L.classify(state, arousal)
        if nxt != state:
            transitions.append((round(t, 2), state, nxt))
            state = nxt
        t += tick
    return arousal, state, transitions


def test_sustained_presence_escalates_to_correction_in_about_4s():
    # net climb = RISE - DECAY = +18/s -> CORRECTION_ENTER (70) in ~3.9 s
    _, state, transitions = _run_loop(0.0, seconds=5.0, detecting=True)
    assert state == "CORRECTION"
    t_corr = [t for t, _, to in transitions if to == "CORRECTION"][0]
    expected = L.CORRECTION_ENTER / (L.AROUSAL_RISE_PER_SEC - L.DECAY_PER_SEC)
    assert abs(t_corr - expected) < 0.2


def test_release_to_calm_takes_about_5s_after_departure():
    # From the correction band, decay-only: (70 -> AROUSAL_EXIT) at 12/s ~ 4.8 s.
    # This is the leaving-reward latency (plus sensor-clear time) - a real
    # conditioning-timing constraint, documented as a tested number.
    _, state, transitions = _run_loop(L.CORRECTION_ENTER, seconds=8.0, detecting=False)
    assert state == "CALM"
    t_calm = [t for t, _, to in transitions if to == "CALM"][0]
    expected = (L.CORRECTION_ENTER - L.AROUSAL_EXIT) / L.DECAY_PER_SEC
    assert abs(t_calm - expected) < 0.3


def test_blip_detection_never_reaches_correction():
    # A 1-second detection blip (e.g. a shadow the confirmation filter let
    # through) must raise at most AROUSAL, never CORRECTION.
    arousal, state, _ = _run_loop(0.0, seconds=1.0, detecting=True)
    assert arousal < L.CORRECTION_ENTER
    assert state != "CORRECTION"
    # and it settles back to CALM on its own
    _, state, _ = _run_loop(arousal, seconds=3.0, detecting=False)
    assert state == "CALM"


# --------------------------------------------------------------------------- #
# Vision latch TTL [OPT-9]
# --------------------------------------------------------------------------- #
def test_vision_latch_sets_and_clears_via_polls():
    v = L.VisionLatch()
    v.poll_result("kitchen-left", now_ms=1000)
    assert v.active and v.zone == "kitchen-left"
    v.poll_result(None, now_ms=2000)         # Pi says NONE -> immediate clear
    assert not v.active and v.zone == ""


def test_vision_latch_expires_when_polls_stop():
    # THE fix: Pi/WiFi dies while the latch is set. The original held arousal
    # up forever (permanent phantom CORRECTION). The TTL must clear it.
    v = L.VisionLatch()
    v.poll_result("kitchen-left", now_ms=1000)
    assert v.service(now_ms=1000 + int(L.cfg.VISION_TTL_MS)) is False  # not yet (needs >)
    assert v.service(now_ms=1001 + int(L.cfg.VISION_TTL_MS)) is True   # expired
    assert not v.active and v.zone == ""


def test_vision_latch_refreshed_by_live_sightings():
    v = L.VisionLatch()
    v.poll_result("kitchen-left", now_ms=0)
    for t in range(1000, 10001, 1000):        # healthy 1 Hz polls, dog present
        v.poll_result("kitchen-left", now_ms=t)
        assert v.service(now_ms=t) is False
    assert v.active


# --------------------------------------------------------------------------- #
# Dispenser fault policy [OPT-11]
# --------------------------------------------------------------------------- #
def test_single_jam_does_not_fault():
    d = L.DispenserHealth()
    d.on_jam()
    assert d.allow_reward()          # one jam could be a mis-seated treat
    assert d.jam_count == 1


def test_consecutive_jams_fault_and_suppress_rewards():
    d = L.DispenserHealth()
    for _ in range(L.JAM_FAULT_THRESHOLD):
        d.on_jam()
    assert d.faulted
    assert not d.allow_reward()      # a marker with no treat must never fire


def test_ack_between_jams_resets_the_streak():
    d = L.DispenserHealth()
    d.on_jam()
    d.on_ack()                       # a good drop in between
    d.on_jam()
    assert not d.faulted             # jams were not consecutive
    assert d.jam_count == 2          # lifetime count still climbs


def test_operator_clear_restores_rewards():
    d = L.DispenserHealth()
    for _ in range(L.JAM_FAULT_THRESHOLD):
        d.on_jam()
    d.operator_clear()               # 'd' after fixing the hopper
    assert d.allow_reward()
    d.on_jam()                       # streak restarted from zero
    assert not d.faulted


# --------------------------------------------------------------------------- #
# Dispense correlation id [C5]
# --------------------------------------------------------------------------- #
def test_matched_ack_is_counted():
    d = L.DispenserHealth()
    rid = d.begin_request()                       # ESP32 sends "DISPENSE <rid>"
    assert d.handle_reply("ACK", rid) is True     # Arduino echoes "ACK <rid>"
    assert d.delivered == 1
    assert d.outstanding_id == 0                  # slot freed


def test_stale_button_ack_is_ignored_not_counted():
    # THE fix: a LOCAL button-press dispense on the Uno replies with sentinel
    # "ACK 0". If it lands while the ESP32 is awaiting the ACK for its own reward
    # request, the id does not match, so it must be discarded - NOT credited to
    # the pending reward. Without the id it would inflate the count by one (the
    # documented ±1 miscount).
    d = L.DispenserHealth()
    rid = d.begin_request()                       # our outstanding reward id
    assert d.handle_reply("ACK", 0) is False      # sentinel button ACK inside the window
    assert d.delivered == 0                       # NOT counted
    assert d.outstanding_id == rid                # our request is still outstanding
    assert d.handle_reply("ACK", rid) is True     # the real ACK still lands
    assert d.delivered == 1


def test_stale_jam_does_not_fault_or_count():
    # A JAM reply with a non-matching id (e.g. a late reply from an already
    # timed-out request) must not touch the jam counters or trip the fault.
    d = L.DispenserHealth()
    d.begin_request()                             # outstanding id 1
    assert d.handle_reply("JAM", 999) is False
    assert d.jam_count == 0
    assert d.consec_jams == 0
    assert not d.faulted


def test_reply_after_timeout_is_ignored():
    d = L.DispenserHealth()
    rid = d.begin_request()
    d.request_timeout()                           # ACK window elapsed, slot freed
    assert d.handle_reply("ACK", rid) is False    # a late reply no longer matches
    assert d.delivered == 0


def test_ids_are_unique_per_request():
    d = L.DispenserHealth()
    first = d.begin_request()
    d.handle_reply("ACK", first)
    second = d.begin_request()
    assert second != first                        # incrementing, so a stale ACK for
    assert d.handle_reply("ACK", first) is False  # the previous id is never re-matched
    assert d.delivered == 1                        # only the first (matched) drop counted


# --------------------------------------------------------------------------- #
# Observation-only build [B5]
# --------------------------------------------------------------------------- #
def test_actuators_gate_follows_observe_flag():
    assert L.actuators_enabled(observe_only=True) is False
    assert L.actuators_enabled(observe_only=False) is True


def test_observe_only_reward_never_dispenses():
    r = L.RewardController(observe_only=True)
    assert r.request(1000) is False
    assert r.request(10_000_000) is False   # not just cooldown - permanently held
    assert r.reward_count == 0              # count untouched: nothing delivered


def test_normal_build_still_rewards():
    r = L.RewardController(observe_only=False)
    assert r.request(1000) is True
    assert r.reward_count == 1


# --------------------------------------------------------------------------- #
# Defensive exposure change - two-step arm/confirm [C4]
# --------------------------------------------------------------------------- #
def test_exposure_requires_confirm_to_change_level():
    e = L.ExposureConfirm(level=0)
    proposed = e.arm(now_ms=0)              # 'm'
    assert proposed == 1
    assert e.armed and e.level == 0         # live level UNCHANGED until confirmed
    assert e.confirm(now_ms=100) is True    # 'y' within window
    assert e.level == 1 and not e.armed


def test_exposure_stray_confirm_without_arm_is_noop():
    e = L.ExposureConfirm(level=0)
    assert e.confirm(now_ms=0) is False     # a lone 'y' does nothing
    assert e.level == 0


def test_exposure_arm_expires_and_cannot_be_committed_late():
    e = L.ExposureConfirm(level=0)
    e.arm(now_ms=0)
    stale = int(L.EXPOSURE_CONFIRM_MS) + 1
    assert e.confirm(now_ms=stale) is False  # too late
    assert e.level == 0                      # welfare knob did NOT flip


def test_exposure_double_arm_from_zero_stays_put_without_confirm():
    # THE §1.7 accident: a double 'm' from level 0. The original landed on 2
    # (approach) with no confirmation; here the LIVE level stays 0 through both
    # arms and only moves on a deliberate 'y'.
    e = L.ExposureConfirm(level=0)
    e.arm(now_ms=0)                          # proposes 1
    e.arm(now_ms=10)                         # re-arm -> proposes 2, still nothing live
    assert e.level == 0
    assert e.confirm(now_ms=20) is True      # explicit confirm
    assert e.level == 2
