"""
control/config.py - controller-only constants + Python mirror of canis_config.h
================================================================================
Two kinds of values live here, clearly separated:

  1. MIRRORS of canis_config.h (the single source of truth). Copied verbatim,
     never invented here. Same unavoidable-mirror situation as
     dashboard/canis_config.py (a .h cannot be imported by Python); the runtime
     anti-drift fix is the firmware's /config announce, which
     MotionController.ingest_config() adopts at runtime so the controller runs
     the values the firmware ACTUALLY runs, not this hand-copied guess.

  2. CONTROLLER-ONLY tunables [new] - estimator noise, comfort-adaptation
     rates, reward window, safety epsilons. These exist ONLY on the Pi, so per
     the repo rule they live here and NOT in canis_config.h.

Import-time guards at the bottom mirror the header's #error style: a bad edit
fails loudly at import, never silently misbehaves at the dog.
"""

import math
import os


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ============================================================================
# 1) MIRRORS of canis_config.h - MUST match the header verbatim
# ============================================================================
# Deployment switch. The firmware side is compile-time; the Python side mirrors
# it via the environment (same pattern as CANIS_CAMERA_MODE) so one deployment
# keeps both in agreement. SAFE DEFAULT: 0 = stationary, NO drive commands.
MOBILE_ENABLED = _env_int("CANIS_MOBILE_ENABLED", 0)      # CANIS_MOBILE_ENABLED

D_MIN_CM = 60                  # CANIS_D_MIN_CM   hard comfort floor (never closer)
D_MAX_CM = 200                 # CANIS_D_MAX_CM   stressed-dog standoff ceiling
CBF_GAMMA = 0.5                # CANIS_CBF_GAMMA  barrier aggressiveness (small = conservative)
CONTROL_RATE_HZ = 20           # CANIS_CONTROL_RATE_HZ
ODOM_STREAM_HZ = 10            # CANIS_ODOM_STREAM_HZ  (/telemetry + /motion poll)
MOTION_CMD_TTL_MS = 600        # CANIS_MOTION_CMD_TTL_MS
MAX_LINEAR_MPS = 0.35          # CANIS_MAX_LINEAR_MPS   hard |v| clamp
MAX_ANGULAR_RPS = 1.5          # CANIS_MAX_ANGULAR_RPS  hard |omega| clamp
ENC_COUNTS_PER_REV = 1320      # CANIS_ENC_COUNTS_PER_REV
WHEEL_DIAMETER_MM = 65.0       # CANIS_WHEEL_DIAMETER_MM
WHEEL_BASE_MM = 140.0          # CANIS_WHEEL_BASE_MM
REWARD_COOLDOWN_MS = 20000     # CANIS_REWARD_COOLDOWN_MS (min gap between treats)
MAX_SESSION_REWARDS = 30       # CANIS_MAX_SESSION_REWARDS (hard cap until reboot)
EXPOSURE_DEFAULT = 0           # CANIS_EXPOSURE_DEFAULT (0 = no motion, SAFE)

# Derived (SI) forms used throughout the controller.
D_MIN_M = D_MIN_CM / 100.0
D_MAX_M = D_MAX_CM / 100.0
WHEEL_CIRCUMFERENCE_M = math.pi * WHEEL_DIAMETER_MM / 1000.0
WHEEL_BASE_M = WHEEL_BASE_MM / 1000.0
CONTROL_DT_S = 1.0 / CONTROL_RATE_HZ

# ============================================================================
# 2) CONTROLLER-ONLY tunables [new]
# ============================================================================

# --- Estimator (control/estimator.py) ---------------------------------------
# Telemetry freshness: the estimate the barrier trusts must be younger than the
# (v, omega) command TTL, so a stale estimate can never certify a fresh MOVE.
TELEM_STALE_S = MOTION_CMD_TTL_MS / 1000.0     # 0.6 s
TELEM_DT_MAX_S = 1.0        # clamp on a single telemetry gap (stall guard, like CANIS_DT_MAX_S)
TS_MS_WRAP = 1 << 32        # ESP32 millis() is uint32 (contract §1.2: "the Pi handles wrap")
HEADING_BLEND = 0.10        # complementary yaw gain per telemetry frame (IMU corrects encoder drift)
ODOM_V_SANE_MPS = 2.0       # reject |odom_v| beyond this (bad frame), fall back to encoder-derived
ODOM_W_SANE_RPS = 8.0       # reject |odom_omega| beyond this

# Dog-range channel (scalar Kalman on d_rd):
TOF_GATE_DEG = 15.0         # ToF return counts as "the dog" only when |beta| <= this
                            # AND the bearing is vision-fresh (spec §2: the camera
                            # gates whether the ToF is ranging the dog or a wall)
TOF_MAX_M = 4.0             # VL53LX practical ceiling; beyond it, ignore the return
TOF_R_VAR = 0.0009          # ToF measurement variance, m^2 (sigma ~3 cm)
VIS_R_VAR_BASE = 0.25       # monocular /detect distance variance at confidence 1.0 (sigma 0.5 m)
VIS_CONF_MIN = 0.10         # floor on confidence used to scale VIS_R (never divide by ~0)
BETA_VIS_GAIN = 0.7         # bearing correction gain from a fresh /detect bearing
DOG_Q_VAR = 0.25            # process-noise growth on d_rd, m^2/s - the DOG can move (~0.5 m/s)
D_VAR_MAX = 25.0            # variance ceiling (fully unknown)
D_UNCERT_CAP_M = 0.30       # barrier uses d - min(sigma, this): uncertainty shrinks h, never grows it
DET_HOLD_S = 3.0            # "dog tracked" window after the last /detect (mirrors dashboard COMMAND_TTL)
DDOT_TAU_S = 0.5            # low-pass time constant on the d_rd derivative estimate
D_MIN_RANGE_M = 0.05        # numerical floor on d in the bearing-propagation division

# --- Adaptive comfort margin (control/comfort.py), spec §5 ------------------
# Latent stress s in [0, 1]; D_comfort = D_MIN + s*(D_MAX - D_MIN).
# s starts at 1.0 (maximum standoff): the controller EARNS proximity from the
# dog's own behavior - it never assumes it (spec §5).
S_INIT = 1.0
S_AROUSAL_PULL_PER_S = 0.5  # first-order pull toward arousal/arousal_max, UPWARD ONLY
S_FLINCH_RATE = 0.20        # per-s GROWTH while the dog retreats fast (flinch)
S_APPROACH_RATE = 0.02      # per-s SHRINK while the dog voluntarily approaches
S_DWELL_RATE = 0.01         # per-s SHRINK while the dog dwells near (having earned it)
FLINCH_DDOT_MPS = 0.25      # d_rd opening faster than this = flinch/retreat
APPROACH_DDOT_MPS = 0.10    # d_rd closing faster than this (robot v<=0) = voluntary approach
DWELL_DDOT_MPS = 0.05       # |ddot| below this = lingering
DWELL_BAND_M = 0.50         # "near" = within D_comfort + this
ROBOT_ADVANCE_EPS = 0.02    # commanded v above this = the ROBOT took the initiative
                            # (clears the dog-earned-proximity credit)

# --- CBF safety filter (control/cbf.py), spec §3-§4 --------------------------
COS_EPS = 1e-3              # |cos(beta)| below this: v cannot change d_rd this tick
CBF_GAMMA_MAX = 2.0         # ingest_config ceiling - an announced gamma is never
                            # adopted above this (tune conservative, spec §9.2)
MOVE_EPS = 1e-3             # |v| and |omega| both below this => the command is a hold => "STOP"

# --- Reward-on-voluntary-approach (control/reward.py), spec §4 --------------
REWARD_WINDOW_S = 2.0       # d_rd must have dropped across this window
REWARD_DROP_M = 0.15        # by at least this much
REWARD_V_EPS = 1e-3         # "own commanded v <= 0" tolerance across the window

# --- Nominal policy (control/motion_controller.py), spec §4 -----------------
FACE_KP = 1.2               # rad/s per rad of bearing error (facing != approaching)
FACE_W_MAX = 0.8            # cap on the facing pivot rate (< MAX_ANGULAR_RPS)

# --- Comfort-margin tick clamp ----------------------------------------------
TICK_DT_MAX_S = 0.5         # clamp on a single controller-tick dt (stall guard)


# ============================================================================
# Import-time guards - mirror canis_config.h's #error style
# ============================================================================
def _guard(cond, msg):
    if not cond:
        raise AssertionError("control/config.py guard failed: " + msg)


_guard(D_MIN_M < D_MAX_M,
       "D_MIN must be < D_MAX (comfort band would be empty or inverted)")
_guard(CBF_GAMMA > 0.0, "CBF_GAMMA must be > 0 (alpha(h) = gamma*h must be class-K)")
_guard(CBF_GAMMA <= CBF_GAMMA_MAX, "CBF_GAMMA above the conservative ceiling")
_guard(TELEM_STALE_S < 1.0,
       "estimator staleness bound must be < the Uno 1000 ms watchdog "
       "(the Pi must go safe FIRST)")
_guard(TELEM_STALE_S <= MOTION_CMD_TTL_MS / 1000.0,
       "a stale estimate must never outlive the motion command it certified")
_guard(S_APPROACH_RATE <= S_FLINCH_RATE and S_DWELL_RATE <= S_FLINCH_RATE,
       "the comfort margin must GROW at least as fast as it shrinks "
       "(never tune toward the dog, spec section 9.2)")
_guard(0.0 < HEADING_BLEND <= 1.0, "HEADING_BLEND must be a (0, 1] blend gain")
_guard(FACE_W_MAX <= MAX_ANGULAR_RPS, "facing pivot cap must respect the hard omega clamp")
_guard(REWARD_WINDOW_S > 0 and REWARD_DROP_M > 0, "reward window/drop must be positive")
_guard(TOF_GATE_DEG > 0 and TOF_MAX_M > 0, "ToF gate must be positive")
_guard(MAX_SESSION_REWARDS > 0, "session reward cap must be positive")
