/*
  canis_config.h — SINGLE SOURCE OF TRUTH for CANIS tunables (firmware side)
  =========================================================================
  Every state-machine / sensor / movement constant lives here ONCE. The three
  original sketches (CANIS_Core, State_Machine_CANIS, State_Machine_WiFi) each
  re-declared these, and they DRIFTED:

    - CANIS_Core.ino:81            CALM_REWARD_MS = 30000
    - State_Machine_CANIS.ino:23   CALM_REWARD_MS = 15000   <-- drift
    - State_Machine_WiFi.ino:38    CALM_REWARD_MS = 15000   <-- drift

  15000 ms is also *wrong*: it is below REWARD_COOLDOWN_MS (20000), so a
  "sustained calm" reward could be requested before the previous treat's
  cooldown expired. The canonical value below is 30000 (>= cooldown), matching
  the corrected CANIS_Core.

  There is a matching Python mirror for the dashboard:
      optimized/dashboard/canis_config.py
  Because a header cannot be imported by Python and a .py cannot be #included by
  C++, two files are unavoidable. To keep them from drifting again, the ESP32
  should POST its live config to the Pi at boot (see the /config note in
  OPTIMIZATIONS.md) so the dashboard displays exactly what the firmware runs,
  instead of a hand-copied guess.

  Arduino build note: the Arduino toolchain only sees headers inside the sketch
  folder (it copies the .ino elsewhere to compile). So this canonical file is
  symlinked into the sketch folder as:
      optimized/CANIS_Core/canis_config.h -> ../canis_config.h
  Edit THIS file; the sketch picks the change up through the symlink.
*/
#ifndef CANIS_CONFIG_H
#define CANIS_CONFIG_H

// ======================= Network / reporting =======================
#define CANIS_PI_PORT              5000
#define CANIS_REPORT_MS            1000UL   // push state to Pi at 1 Hz (plus on change)
#define CANIS_POLL_MS              1000UL   // poll Pi for vision commands at 1 Hz
#define CANIS_HTTP_TIMEOUT_MS      300      // short: never stall the control loop
#define CANIS_POST_BACKOFF_MS      5000UL   // pause POST/poll this long if the Pi is dead

// WiFi reconnect watchdog (new): how often loop() re-checks the link and, if
// dropped, kicks a non-blocking WiFi.reconnect().
#define CANIS_WIFI_CHECK_MS        5000UL

// Vision-detection local TTL [OPT-9] (NEW): visionActive is a latch that is
// normally cleared by a successful /command poll returning "NONE". If WiFi or
// the Pi dies while the latch is set, the original held arousal up FOREVER —
// permanent CORRECTION (buzzer + motion at exposure >= 1) at a dog that may
// have left long ago. This TTL clears the latch locally when the poll stops
// refreshing it. Must be > 2x CANIS_POLL_MS so a single missed poll doesn't
// flap the detection.
#define CANIS_VISION_TTL_MS        4000UL

// ======================= Sensor tunables (from Sensors_Test) =======================
#define CANIS_CONFIRM_N            3        // consecutive agreeing reads to flip confirmed
#define CANIS_SAMPLE_MS            100      // 10 Hz sampling -> 3 reads = ~300 ms to confirm
#define CANIS_ZONE_CM              30.0f    // "in the forbidden zone" if closer than this
#define CANIS_WARMUP_MS            60000UL  // ignore PIR for the first 60 s
#define CANIS_ECHO_TIMEOUT_US      12000UL  // ~2 m cap; the close-range zone needs no more
#define CANIS_NUM_US               3        // set to 2 to disable the third ultrasonic

// ======================= Arousal / state tunables =======================
#define CANIS_AROUSAL_MAX          100.0f
#define CANIS_DECAY_PER_SEC        12.0f    // bleed-off when nothing is detected
#define CANIS_TRIGGER_BUMP         25.0f    // manual 't' key bump (bench testing only)
#define CANIS_AROUSAL_RISE_PER_SEC 30.0f    // confirmed detection drives arousal UP (must beat decay)

// --- Schmitt-trigger (hysteresis) thresholds (NEW) ---------------------------
// The originals used ONE threshold per boundary, so a dog hovering right at 70
// (or 20) chattered CORRECTION on/off every frame. Separate enter/exit levels
// give a dead-band: you must climb past ENTER to turn a state ON, and fall
// below EXIT to turn it back OFF. EXIT must be strictly < ENTER.
// Integer literals (not 70.0f) so the compile-time #if guards below are legal —
// the C preprocessor only evaluates integer constant expressions. They promote
// to float automatically in the arousal comparisons/math, so behavior is identical.
#define CANIS_CORRECTION_ENTER     70       // AROUSAL -> CORRECTION when arousal >= this
#define CANIS_CORRECTION_EXIT      60       // CORRECTION -> AROUSAL only when arousal < this
#define CANIS_AROUSAL_ENTER        20       // CALM -> AROUSAL when arousal >= this
#define CANIS_AROUSAL_EXIT         12       // AROUSAL -> CALM only when arousal < this

// dt clamp (NEW): a WiFi/HTTP stall can make one loop's dt huge, teleporting
// arousal straight into CORRECTION. Cap dt so a stall can add at most
// (RISE-DECAY) * DT_MAX to arousal in a single tick.
#define CANIS_DT_MAX_S             0.2f

// ======================= Output tunables =======================
#define CANIS_CORRECTION_TONE_FREQ 800      // disengage cue (distinct from 2000 Hz reward marker)
#define CANIS_CORRECTION_TONE_MS   150
#define CANIS_CORRECTION_ACTION_MS 2000UL   // min gap between correction cue cycles

// ======================= Movement / exposure (welfare gate) =======================
// 0 = no movement (buzzer only, SAFE DEFAULT), 1 = orient only, 2 = orient + approach.
#define CANIS_EXPOSURE_DEFAULT     0
#define CANIS_MOVE_PULSE_MS        500UL    // re-send cadence; MUST stay < Arduino MOTION_TIMEOUT_MS (1000)
#define CANIS_ORIENT_MS            700UL    // pivot-toward-zone duration per correction cycle
#define CANIS_APPROACH_MS          400UL    // brief forward nudge after orienting (level 2 only)

// Exposure change is a two-step ARM->CONFIRM commit [C4] (ENGINEERING_REVIEW
// §1.7): the ORIGINAL cycled 0->1->2->0 on a single 'm' keypress, so a stray
// double-press from level 0 landed on 2 (approach enabled) with no confirmation
// — the worst possible accidental transition on the most welfare-critical knob.
// Now 'm' only PROPOSES the next level (arms it) and a distinct 'y' commits it;
// an un-confirmed proposal auto-expires after this window so a stale arm can
// never be committed later by an unrelated keypress. (Dashboard mirrors this
// with /exposure/arm + /exposure/confirm and a visible session arm state.)
#define CANIS_EXPOSURE_CONFIRM_MS  5000UL   // arm expires if not confirmed within this

// ======================= Observation-only build [B5] =======================
// CANIS_OBSERVE_ONLY compiles the robot into a PURE SENSOR: the full pipeline
// (sensors -> confirmation -> arousal -> state machine -> 1 Hz dashboard report)
// runs completely unchanged, but every ACTUATOR is gated off — no treat
// dispense, no correction buzzer, no motion. This replaces the "unplug the
// dispenser link / run the hopper empty" improvisation the baseline week used
// for its overnight false-positive probe (STUDY_PROTOCOL.md Phase A1 / ROADMAP
// B3): the state trace is still recorded, so you can measure how often the
// robot WOULD have intervened, with zero risk of a stray cue reaching the dog.
// Default 0 = normal build. Set to 1 only for observation/baseline sessions.
#define CANIS_OBSERVE_ONLY         0

// ======================= Reward safety =======================
#define CANIS_REWARD_COOLDOWN_MS   20000UL  // min gap between any two treats
#define CANIS_MAX_SESSION_REWARDS  30       // hard cap until reboot
#define CANIS_CALM_REWARD_MS       30000UL  // sustained-calm reward interval; MUST be >= REWARD_COOLDOWN_MS

// ======================= Dispense-ACK (NEW, optional) =======================
// If the Arduino echoes "ACK" after a completed drop, the ESP32 counts the
// reward only on ACK (delivery) rather than on send (intent). Toggle off to
// keep the original count-on-send behavior on hardware that can't ACK.
#define CANIS_USE_DISPENSE_ACK     1
// ACK window sized for the WORST case on the Arduino with jam detection [OPT-11]
// enabled: marker (120) + pre-drop (150) + gate (150) + drop wait (600) +
// retry gate (150) + retry wait (600) ~= 1.8 s. 2.5 s gives margin.
#define CANIS_ACK_TIMEOUT_MS       2500UL   // if no ACK/JAM within this window, log a miss

// ======================= Dispenser fault policy [OPT-11] =======================
// If the Arduino reports "JAM" (beam-break saw no treat fall, even after one
// retry) this many times IN A ROW, the ESP32 stops requesting treats — a
// marker tone with no treat actively damages the conditioning, so a faulted
// dispenser must go silent, not keep beeping. Manual 'd' clears the fault
// after the hopper is fixed. (Arduino-side tunables live in the dispenser
// sketch: JAM_SENSOR_ENABLED, DROP_TIMEOUT_MS, JAM_RETRY.)
#define CANIS_JAM_FAULT_THRESHOLD  2

// Compile-time sanity checks so a bad edit fails loudly instead of misbehaving.
#if CANIS_CALM_REWARD_MS < CANIS_REWARD_COOLDOWN_MS
#error "CALM_REWARD_MS must be >= REWARD_COOLDOWN_MS (a calm reward would fire inside cooldown)"
#endif
#if CANIS_CORRECTION_EXIT >= CANIS_CORRECTION_ENTER
#error "CORRECTION_EXIT must be < CORRECTION_ENTER for hysteresis"
#endif
#if CANIS_AROUSAL_EXIT >= CANIS_AROUSAL_ENTER
#error "AROUSAL_EXIT must be < AROUSAL_ENTER for hysteresis"
#endif
#if CANIS_MOVE_PULSE_MS >= 1000UL
#error "MOVE_PULSE_MS must be < Arduino MOTION_TIMEOUT_MS (1000) or motion stutters"
#endif
#if CANIS_VISION_TTL_MS <= 2 * CANIS_POLL_MS
#error "VISION_TTL_MS must exceed 2x POLL_MS or one missed poll flaps the vision latch"
#endif

#endif  // CANIS_CONFIG_H
