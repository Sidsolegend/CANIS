"""
canis_logic.py — pure-Python mirror of the CANIS firmware control logic
=======================================================================
Hardware-free extraction of the decision logic that lives in
`optimized/CANIS_Core/CANIS_Core.ino` (and, on the Python side, the dashboard
simulator in `optimized/dashboard/app.py`). Pulling it into one importable,
side-effect-free module lets the state machine, hysteresis, dt clamp, reward
safety, and the level-1 "never approach" safety fix be unit-tested off-device.

Thresholds are imported from the dashboard's `canis_config.py` (which itself
mirrors `canis_config.h`) so this module cannot silently drift from the firmware.
The few firmware-only tunables that have no dashboard equivalent (arousal RISE,
the dt clamp, session reward cap) are declared here with the values from
`optimized/canis_config.h`.
"""

import os
import sys

# Import the dashboard config mirror so hysteresis thresholds stay single-sourced.
_DASH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dashboard"))
if _DASH not in sys.path:
    sys.path.insert(0, _DASH)
import canis_config as cfg  # noqa: E402

# ---- thresholds mirrored from canis_config.h via canis_config.py -------------
AROUSAL_MAX = cfg.AROUSAL_MAX
DECAY_PER_SEC = cfg.DECAY_PER_SEC
AROUSAL_ENTER = cfg.AROUSAL_ENTER
AROUSAL_EXIT = cfg.AROUSAL_EXIT
CORRECTION_ENTER = cfg.CORRECTION_ENTER
CORRECTION_EXIT = cfg.CORRECTION_EXIT
REWARD_COOLDOWN_MS = cfg.REWARD_COOLDOWN_MS

# ---- firmware-only tunables, now sourced from canis_config.py too ------------
# (Previously hardcoded here as a third copy; moved into the config mirror so
# tests, the dashboard, and the analyzer all read one Python-side place.)
AROUSAL_RISE_PER_SEC = cfg.AROUSAL_RISE_PER_SEC
DT_MAX_S = cfg.DT_MAX_S
MAX_SESSION_REWARDS = cfg.MAX_SESSION_REWARDS
JAM_FAULT_THRESHOLD = cfg.JAM_FAULT_THRESHOLD
OBSERVE_ONLY = cfg.OBSERVE_ONLY               # [B5] pure-sensor build flag
EXPOSURE_DEFAULT = cfg.EXPOSURE_DEFAULT        # [C4] safe default level
EXPOSURE_CONFIRM_MS = cfg.EXPOSURE_CONFIRM_MS  # [C4] arm->confirm window

STATES = ("CALM", "AROUSAL", "CORRECTION")


def actuators_enabled(observe_only=OBSERVE_ONLY):
    """[B5] Master actuator gate — mirror of the CANIS_OBSERVE_ONLY guards in
    reward() and runStateBehavior(). In an observation-only build every output
    (treat dispense, correction buzzer, orient/approach motion) is suppressed
    while the sense -> arousal -> state -> 1 Hz report pipeline runs completely
    unchanged, so the baseline probe records what the robot WOULD have done with
    zero risk of a stray cue reaching the dog. Returns True iff actuators fire."""
    return not bool(observe_only)


def classify(current, a):
    """Schmitt-trigger state machine — mirror of classify() in CANIS_Core.ino
    and Simulator._classify() in the dashboard. Enter thresholds raise a state,
    exit thresholds (strictly lower) lower it, giving a stable dead-band."""
    if current == "CALM":
        if a >= CORRECTION_ENTER:
            return "CORRECTION"
        if a >= AROUSAL_ENTER:
            return "AROUSAL"
        return "CALM"
    if current == "AROUSAL":
        if a >= CORRECTION_ENTER:
            return "CORRECTION"
        if a < AROUSAL_EXIT:
            return "CALM"
        return "AROUSAL"
    # CORRECTION
    if a < AROUSAL_EXIT:
        return "CALM"
    if a < CORRECTION_EXIT:
        return "AROUSAL"
    return "CORRECTION"


def update_arousal(arousal, dt, detecting):
    """Mirror of updateArousal() in CANIS_Core.ino, including the [OPT-4] dt clamp
    that stops a WiFi/HTTP stall from teleporting arousal into CORRECTION."""
    if dt > DT_MAX_S:
        dt = DT_MAX_S
    if detecting:
        arousal += AROUSAL_RISE_PER_SEC * dt
    arousal -= DECAY_PER_SEC * dt
    if arousal > AROUSAL_MAX:
        arousal = AROUSAL_MAX
    if arousal < 0:
        arousal = 0.0
    return arousal


def safe_orient_dir(direction, exposure_level):
    """Mirror of the [OPT-8][SAFETY] level-1 fix in runStateBehavior().

    Welfare invariant: at EXPOSURE_LEVEL < 2 the robot may orient/pivot only and
    must NEVER approach. localBearing()/zoneToDir() can hand back "FORWARD"
    (centre ultrasonic nearest, or the zoneToDir default), which the Arduino
    would drive straight at the dog. Below level 2 a FORWARD bearing is rewritten
    to a token in-place pivot ("LEFT"); level >= 2 is unchanged.
    """
    if exposure_level < 2 and direction == "FORWARD":
        return "LEFT"
    return direction


class RewardController:
    """Mirror of reward() cooldown + session cap (the count-on-send path).

    [B5] observe_only mirrors an OBSERVE_ONLY build: reward() returns before it
    can dispense, so no treat ever fires (the sensing/report path is unaffected).
    """

    def __init__(self, observe_only=False):
        self.reward_count = 0
        self.last_reward_ms = 0
        self.observe_only = bool(observe_only)

    def request(self, now_ms):
        """Return True if a treat is actually dispensed, False if held."""
        if self.observe_only:
            return False  # [B5] observation-only: sense + report, never dispense
        if self.last_reward_ms != 0 and now_ms - self.last_reward_ms < REWARD_COOLDOWN_MS:
            return False  # within cooldown
        if self.reward_count >= MAX_SESSION_REWARDS:
            return False  # session cap reached
        self.last_reward_ms = now_ms
        self.reward_count += 1
        return True


class ExposureConfirm:
    """Mirror of the firmware's [C4] two-key defensive exposure change. The
    original cycled 0->1->2->0 on a single 'm' press, so a stray double-press
    from level 0 landed on 2 (approach enabled) unconfirmed — the worst possible
    accidental transition on the most welfare-critical knob (§1.7).

    Now 'm' only ARMS the next level (arm(); does NOT touch the live level) and a
    distinct 'y' COMMITS it (confirm()) — but only while the arm is still within
    EXPOSURE_CONFIRM_MS. A stale or absent proposal is discarded, so no lone
    keypress can ever flip the level.
    """

    def __init__(self, level=EXPOSURE_DEFAULT):
        self.level = int(level)      # the live EXPOSURE_LEVEL
        self._proposed = None        # armed next level, or None
        self._armed_ms = 0

    def arm(self, now_ms):
        """'m': propose the next level in the 0->1->2->0 cycle. Repeated arms
        cycle the PROPOSAL (so any target level can be armed directly from any
        live level); a stale proposal restarts the cycle from the live level.
        Returns the proposed level."""
        if self._proposed is not None and (now_ms - self._armed_ms) <= EXPOSURE_CONFIRM_MS:
            self._proposed = (self._proposed + 1) % 3
        else:
            self._proposed = (self.level + 1) % 3
        self._armed_ms = now_ms
        return self._proposed

    def confirm(self, now_ms):
        """'y': commit iff a fresh proposal is armed. Returns True if the level
        actually changed. Always clears the proposal (mirrors the firmware
        resetting proposedExposure to -1 after any 'y')."""
        changed = False
        if self._proposed is not None and (now_ms - self._armed_ms) <= EXPOSURE_CONFIRM_MS:
            self.level = self._proposed
            changed = True
        self._proposed = None
        return changed

    @property
    def armed(self):
        return self._proposed is not None


class VisionLatch:
    """Mirror of the [OPT-9] vision-detection TTL in CANIS_Core.ino.

    visionActive is a latch normally cleared by a successful /command poll
    returning "NONE". If the network dies while it is set, the ORIGINAL
    firmware held arousal up forever (permanent CORRECTION at a dog that may
    have left). The optimized firmware clears the latch locally when no
    successful poll has refreshed it within VISION_TTL_MS.
    """

    def __init__(self):
        self.active = False
        self.zone = ""
        self.last_refresh_ms = 0

    def poll_result(self, zone_or_none, now_ms):
        """A successful /command poll arrived. zone_or_none: str zone or None."""
        self.last_refresh_ms = now_ms
        if zone_or_none:
            self.active = True
            self.zone = zone_or_none
        else:
            self.active = False
            self.zone = ""

    def service(self, now_ms):
        """Called every loop. Expires a stale latch; returns True if it expired."""
        if self.active and now_ms - self.last_refresh_ms > cfg.VISION_TTL_MS:
            self.active = False
            self.zone = ""
            return True
        return False


class DispenserHealth:
    """Mirror of the [OPT-11] ESP32-side jam-fault policy plus the [C5]
    correlation-id gate in serviceAck().

    The Arduino replies "ACK <id>" (treat confirmed dropped) or "JAM <id>"
    (beam-break saw nothing fall, even after one retry). JAM_FAULT_THRESHOLD
    consecutive JAMs mark the dispenser FAULTED: further reward requests are
    suppressed, because a marker tone with no treat actively damages the
    conditioning. A manual operator action ('d' on the serial console) clears
    the fault.

    [C5] Each link-triggered reward carries an incrementing correlation id
    (begin_request()); the Arduino echoes it back, and handle_reply() only
    counts a reply whose id matches the currently-outstanding request. A reply
    with a stale or sentinel id — e.g. "ACK 0" from a LOCAL button-press
    dispense that lands inside the wait window — is discarded, not counted,
    fixing the documented ±1 miscount (TECH_DEBT #4).
    """

    def __init__(self):
        self.jam_count = 0        # lifetime jams this session (reported to Pi)
        self.consec_jams = 0
        self.faulted = False
        self.delivered = 0        # [C5] treats confirmed via a MATCHED ACK (mirror of rewardCount)
        self.next_id = 0          # [C5] last-issued reward correlation id (0 reserved as sentinel)
        self.outstanding_id = 0   # [C5] id we're awaiting a reply for (0 = none outstanding)

    def begin_request(self):
        """Mirror of reward()'s [C5] id assignment: allocate the next correlation
        id (ids start at 1) and mark it outstanding. Returns the id the firmware
        sends as "DISPENSE <id>"."""
        self.next_id += 1
        self.outstanding_id = self.next_id
        return self.outstanding_id

    def handle_reply(self, kind, rid):
        """Mirror of serviceAck()'s [C5] correlation gate. A reply ("ACK"/"JAM")
        is applied only if its id matches the outstanding request; a stale or
        sentinel (id 0) reply is discarded. Returns True iff it was accepted."""
        if self.outstanding_id != 0 and rid == self.outstanding_id:
            if kind == "ACK":
                self.on_ack()
            elif kind == "JAM":
                self.on_jam()
            self.outstanding_id = 0
            return True
        return False   # unmatched / stale / sentinel -> ignored, nothing counted

    def request_timeout(self):
        """Mirror of serviceAck()'s deadline branch: an unanswered request frees
        the slot so a later reply with that id no longer matches."""
        self.outstanding_id = 0

    def on_ack(self):
        self.delivered += 1
        self.consec_jams = 0

    def on_jam(self):
        self.jam_count += 1
        self.consec_jams += 1
        if self.consec_jams >= JAM_FAULT_THRESHOLD:
            self.faulted = True

    def allow_reward(self):
        return not self.faulted

    def operator_clear(self):
        self.faulted = False
        self.consec_jams = 0


# ---- [C7] commissioning calibration mirrors -------------------------------- #
# Pure mirrors of the Uno-side bring-up calibration in
# Treat_Dispenser_CANIS.ino. These constants live in that SKETCH (the Uno does
# NOT include canis_config.h — it is a standalone sketch), so they are mirrored
# here directly rather than via canis_config.py.
TRIM_MAX_DEG = 15    # mirror of Treat_Dispenser_CANIS.ino TRIM_MAX_DEG
TRIM_STEP_DEG = 5    # mirror of TRIM_STEP_DEG (one TRIM+/TRIM- nudge)
SERVO_MIN_DEG = 0    # SG90 mechanical floor (constrain() bound)
SERVO_MAX_DEG = 180  # SG90 mechanical ceiling


def apply_motor_invert(speed, invert):
    """Mirror of the [C7] XOR at the top of setMotor(): a persisted per-motor
    invert flag flips the direction sign so a wrong-way wheel is fixed in software
    (INVL/INVR) instead of physically swapping the motor's two leads."""
    return -speed if invert else speed


def motor_outputs(speed):
    """Mirror of setMotor()'s sign -> (IN_A, IN_B) decision. Returns the digital
    levels written to the L298N input pair for a given signed speed."""
    if speed > 0:
        return ("HIGH", "LOW")
    if speed < 0:
        return ("LOW", "HIGH")
    return ("LOW", "LOW")


def motor_direction(speed, invert):
    """Full mirror of setMotor()'s direction stage: apply the invert flag, then
    resolve the IN-pair levels. This is what the driver actually energizes."""
    return motor_outputs(apply_motor_invert(speed, invert))


def clamp_trim(offset):
    """Mirror of clampTrim(): the persisted flap-zero trim may not exceed
    +/-TRIM_MAX_DEG, so a bad TRIM+ spam can never drive the servo into a stall."""
    if offset > TRIM_MAX_DEG:
        return TRIM_MAX_DEG
    if offset < -TRIM_MAX_DEG:
        return -TRIM_MAX_DEG
    return offset


def nudge_trim(current, step):
    """Mirror of nudgeTrim(): one TRIM+/TRIM- step, re-clamped to the legal range
    so repeated nudges saturate at +/-TRIM_MAX_DEG rather than running away."""
    return clamp_trim(current + step)


def trimmed_angle(base, offset):
    """Mirror of closedAngle()/openAngle(): CLOSED_ANGLE/OPEN_ANGLE shifted by the
    (clamped) trim, then constrained to the servo's [0,180] mechanical range."""
    a = base + clamp_trim(offset)
    if a < SERVO_MIN_DEG:
        return SERVO_MIN_DEG
    if a > SERVO_MAX_DEG:
        return SERVO_MAX_DEG
    return a
