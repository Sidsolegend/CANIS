"""
canis_config.py — Python mirror of optimized/canis_config.h
===========================================================
The dashboard previously RE-TYPED the state-machine constants inline
(dashboard/app.py:29-33), a fourth hand-maintained copy that could (and did)
drift from the firmware. This module centralizes the Python side so app.py and
the simulator read one place.

A header (.h) and a module (.py) cannot share a literal file, so this mirror is
unavoidable. The real anti-drift fix is runtime, not source: the ESP32 should
POST its live config to a /config endpoint at boot, and the dashboard should
prefer those values (see OPTIMIZATIONS.md). Until then, these MUST match
optimized/canis_config.h — the values below are copied from it verbatim.
"""

# --- Arousal / state (mirror of canis_config.h) ---
AROUSAL_MAX = 100.0
DECAY_PER_SEC = 12.0
TRIGGER_BUMP = 25.0

# Hysteresis thresholds (match the firmware Schmitt trigger).
AROUSAL_ENTER = 20.0
AROUSAL_EXIT = 12.0
CORRECTION_ENTER = 70.0
CORRECTION_EXIT = 60.0

# Arousal integrator (mirror of the firmware seam + [OPT-4] dt clamp).
# Previously these lived only in tests/canis_logic.py — a third hand-typed copy.
AROUSAL_RISE_PER_SEC = 30.0    # CANIS_AROUSAL_RISE_PER_SEC
DT_MAX_S = 0.2                 # CANIS_DT_MAX_S

# --- Reward timing / safety (mirror) ---
CALM_REWARD_MS = 30000
REWARD_COOLDOWN_MS = 20000
MAX_SESSION_REWARDS = 30       # CANIS_MAX_SESSION_REWARDS

# --- Vision latch / fault policy (mirror) ---
VISION_TTL_MS = 4000           # CANIS_VISION_TTL_MS   [OPT-9]
JAM_FAULT_THRESHOLD = 2        # CANIS_JAM_FAULT_THRESHOLD [OPT-11]

# --- Exposure / welfare gate (mirror) ---
EXPOSURE_DEFAULT = 0           # CANIS_EXPOSURE_DEFAULT (0=buzzer only, 1=orient, 2=approach)
EXPOSURE_CONFIRM_MS = 5000     # CANIS_EXPOSURE_CONFIRM_MS  two-step arm->confirm window [C4]
OBSERVE_ONLY = 0               # CANIS_OBSERVE_ONLY  1 = pure-sensor build, all actuators off [B5]

# --- Dashboard-only knobs (no firmware equivalent) ---
STALE_AFTER = 5.0    # seconds without an ESP32 POST before falling back to sim
COMMAND_TTL = 3.0    # seconds a vision detection stays "current" without a new POST

# --- Event log (dashboard-only; longitudinal-study instrumentation) ---
# Directory override: CANIS_LOG_DIR env var. File: events.jsonl (append-only).
EVENT_LOG_FILE = "events.jsonl"
ZONE_EPISODE_GAP_S = 3.0   # zone_seen events further apart than this start a new episode
                           # (matches COMMAND_TTL: the detection would have expired)

# --- Input-validation bounds for /ingest (defensive server-side limits) ---
AROUSAL_MIN = 0.0
COUNT_MAX = 1_000_000          # sane ceiling for reward/intervention/uptime counters
VALID_STATES = ("CALM", "AROUSAL", "CORRECTION")
ZONE_MAX_LEN = 32              # longest zone label the ESP32 buffer can hold (visionZone[24]) + slack
