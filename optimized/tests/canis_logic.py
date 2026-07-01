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

# ---- firmware-only tunables (from optimized/canis_config.h) ------------------
AROUSAL_RISE_PER_SEC = 30.0     # CANIS_AROUSAL_RISE_PER_SEC
DT_MAX_S = 0.2                  # CANIS_DT_MAX_S  (dt clamp)
MAX_SESSION_REWARDS = 30        # CANIS_MAX_SESSION_REWARDS

STATES = ("CALM", "AROUSAL", "CORRECTION")


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
    """Mirror of reward() cooldown + session cap (the count-on-send path)."""

    def __init__(self):
        self.reward_count = 0
        self.last_reward_ms = 0

    def request(self, now_ms):
        """Return True if a treat is actually dispensed, False if held."""
        if self.last_reward_ms != 0 and now_ms - self.last_reward_ms < REWARD_COOLDOWN_MS:
            return False  # within cooldown
        if self.reward_count >= MAX_SESSION_REWARDS:
            return False  # session cap reached
        self.last_reward_ms = now_ms
        self.reward_count += 1
        return True
