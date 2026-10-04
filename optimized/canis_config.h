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
// Minimum gap between WiFi.reconnect() kicks. Joining can take 20+ s on a weak signal; re-kicking every
// CHECK_MS (5 s) restarts the attempt before it can finish and the link never comes up (seen on the bench
// 2026-10). Must be >= CHECK_MS.
#define CANIS_WIFI_RETRY_MS        30000UL
// After associating with the AP, wait this long for DHCP to hand out an IP before kicking the link again.
#define CANIS_WIFI_DHCP_WAIT_MS    90000UL
// Optional FIXED IP for the ESP32, skipping DHCP. On the bench (2026-10) the ESP32 associates at about -71 dBm but never
// receives a DHCP lease. "" = use DHCP (the default). The address must be unused and outside the router's DHCP pool.
#define CANIS_WIFI_STATIC_IP       "192.168.86.15"
#define CANIS_WIFI_GATEWAY         "192.168.86.1"
#define CANIS_WIFI_SUBNET          "255.255.255.0"

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
// Ultrasonic trigger pulse width. 10 us is the HC-SR04 standard. The SR04M-2 datasheet says "> 10 us"; 20 did NOT help it
// on the bench (2026-10) and RIGHT stopped answering around the time it was tried, so it is back to 10.
#define CANIS_TRIG_PULSE_US        10
#define CANIS_NUM_US               3        // sensor count; array order is LEFT, CENTRE, RIGHT, so 2 would drop RIGHT
// 1 = the SR04M-2 (CENTRE, forward) is wired and read (default). 0 = it is NOT wired and the
// firmware skips it (no floating-echo phantom readings; self-test says SKIPPED).
#define CANIS_US_CENTRE_ENABLED    1

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

// ======================= Roaming / patrol (house wander) =======================
// CANIS_ROAM_ENABLED turns CANIS from a PLACED SENTRY into a reactive house
// ROAMER: while no dog is seen it wanders, steering off obstacles with the 3
// ultrasonics; the instant the CAMERA sees Shifu's marker it STOPS, holds, and
// runs the normal detect -> correct -> reward intervention, then (after a short
// settle) resumes wandering. It never drives AT the dog — engagement is always
// stationary; a dog off to the side is just avoided as an obstacle.
//
// CRITICAL: while roaming, arousal is driven by the CAMERA MARKER ONLY. The
// ultrasonics can't tell a dog from a wall and PIR can't tell the dog from the
// robot's own motion, so feeding either into arousal while moving would fire
// phantom corrections at every wall. Dog detection therefore needs the ArUco
// marker in the FORWARD camera's view — coverage is "what CANIS is facing."
//
// WELFARE NOTE: a moving robot is exactly what a fearful dog fears, so roaming
// changes the project's posture. Set to 0 to revert to the stationary sentry.
#define CANIS_ROAM_ENABLED     1
#define CANIS_ROAM_CLEAR_CM    35.0f   // obstacle nearer than this (any ultrasonic) -> steer away
#define CANIS_ROAM_FWD_MS      600UL   // forward leg length between steering decisions
#define CANIS_ROAM_TURN_MS     450UL   // pivot length when avoiding an obstacle
#define CANIS_ROAM_RESUME_MS   4000UL  // stay put this long after an engagement before roaming again

// ======================= Behaviour mode + never-approach =======================
// Two live behaviours, toggled at runtime with 'g' / 'p' over Serial:
//   GUARD  — stationary sentry at an off-limits spot: dog detected -> buzzer
//            disengage cue + treat when he leaves. No roaming.
//   PLAY   — enrichment/companion: roams, and when Shifu engages (his marker in
//            view, or he comes close) it drops a treat and FLEES so he chases.
//            This is also counter-conditioning: moving robot + treats = "good".
#define CANIS_MODE_GUARD       0
#define CANIS_MODE_PLAY        1
#define CANIS_MODE_PLACE       2   // "good boy": reward Shifu for staying on his bed/mat
#define CANIS_MODE_DEFAULT     CANIS_MODE_PLACE  // stationary reward-for-presence (safe for fearful dog); 'g'/'p'/'b' switch live

// HARD INVARIANT: the robot NEVER drives toward the dog. Every dog-directed
// motion is a RETREAT (turn away, or back straight off). This disables the
// level-2 forward "approach" entirely and makes guard-correction buzzer-only.
// Set 0 only if you deliberately want the old orient/approach behaviour.
#define CANIS_NEVER_APPROACH   1
#define CANIS_PLAY_FLEE_MS     700UL   // retreat leg when the dog engages (play)

// PLACE mode: point the robot at Shifu's bed/mat; treat him for settling there.
#define CANIS_PLACE_SETTLE_MS  6000UL   // he must be on the mat this long before the 1st treat
#define CANIS_PLACE_REWARD_MS  45000UL  // then a treat this often while he stays (>= REWARD_COOLDOWN_MS)

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
// GUARD's sustained-calm treat also requires that the camera saw Shifu within this window, so it
// cannot drop treats into an empty house ("calm" only means "nothing detected"; no sensor can tell
// an empty room from a dog who is staying away). 0 = disabled (old behaviour: fires whenever CALM).
#define CANIS_CALM_PRESENCE_WINDOW_MS  600000UL   // 10 min

// ======================= Dispense-ACK (NEW, optional) =======================
// If the Arduino echoes "ACK" after a completed drop, the ESP32 counts the
// reward only on ACK (delivery) rather than on send (intent). Toggle off to
// keep the original count-on-send behavior on hardware that can't ACK.
#define CANIS_USE_DISPENSE_ACK     0  // [bench 2026-09] no 1k/2k divider wired for the
                                       // pin11->GPIO4 return line — count-on-send instead
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

// ======================= Mobile drivetrain + CBF safe motion (NEW) =======================
// Everything in this block serves the encoder-drivetrain / control-barrier-
// function upgrade specified in docs/SAFE_MOTION_CBF_SPEC.md, with wire
// formats locked in docs/INTERFACE_CONTRACT.md. NONE of it changes existing
// behavior unless CANIS_MOBILE_ENABLED is 1: with the flag at 0 (SAFE
// DEFAULT) the ESP32 never polls /motion, never emits the "VEL" grammar to
// the Arduino, and never streams /telemetry — the build above runs untouched
// (YOLO/vision, dispenser, dashboard, roam-per-CANIS_ROAM_ENABLED included).
#define CANIS_MOBILE_ENABLED       0        // 0 = stationary (SAFE DEFAULT), 1 = CBF differential drive

// Quadrature encoder inputs (ESP32 PCNT peripheral — counts in hardware, zero
// CPU cost; this is WHY the encoders land on the ESP32 and not the Uno).
// Pin budget: 27 (PIR), 13 (Serial2 link TX), 32/34, 26/25, 14/35 (ultrasonics)
// are taken; 21/22 are RESERVED for I2C (IMU + VL53LX ToF).
// [bench 2026-09] The ESP32 sits on a breadboard that covers every hole beside
// the 16-19 / 3V3 side, so the encoders are REMAPPED to pins reachable on the
// other side: 33 (freed — the ESP32 correction buzzer is unwired, BUZZER_PIN=-1
// in CANIS_Core.ino), 36/39 (input-only, no internal pull-ups — the encoder
// boards must drive their outputs themselves) and 27 (the old PIR pin — the PIR
// was removed, PIR_PIN=-1 in CANIS_Core.ino). Avoid GPIO 12: it is a BOOT
// STRAPPING pin and a HIGH on it at reset can stop the ESP32 booting.
// (Original WROOM defaults were 18/19 left, 16/17 right.)
#define CANIS_ENC_L_A_PIN          33       // left encoder channel A
#define CANIS_ENC_L_B_PIN          27       // left encoder channel B (quadrature -> signed counts)
#define CANIS_ENC_R_A_PIN          36       // right encoder channel A (input-only)
#define CANIS_ENC_R_B_PIN          39       // right encoder channel B (input-only)

// Drivetrain geometry — MEASURE these on the real chassis before trusting any
// odometry number the barrier layer consumes; a wrong wheel base silently
// corrupts omega and the pose estimate downstream.
#define CANIS_ENC_COUNTS_PER_REV   1320     // counts per WHEEL revolution at 4x quadrature decode
                                            // (e.g. 11 PPR motor x 4 edges x 30:1 gearbox = 1320)
#define CANIS_WHEEL_DIAMETER_MM    65.0f    // drive wheel diameter
#define CANIS_WHEEL_BASE_MM        140.0f   // track width, wheel-center to wheel-center

// Comfort-distance band for the barrier (SAFE_MOTION_CBF_SPEC.md §5): the
// adaptive margin D_comfort(s) lives in [D_MIN, D_MAX]. D_MIN is the floor the
// robot may NEVER close below even for a fully habituated dog; D_MAX is the
// standoff for a maximally stressed one. Integer literals (not 60.0f) so the
// compile-time #if guards below are legal — same trick as the hysteresis
// thresholds above; they promote to float in the distance math.
#define CANIS_D_MIN_CM             60       // hard comfort floor (ToF-measured robot->dog range)
#define CANIS_D_MAX_CM             200      // stressed-dog standoff ceiling

// Barrier aggressiveness gamma in the CBF constraint hdot >= -gamma*h.
// SMALL gamma = conservative: the robot starts yielding distance while still
// far from the boundary. Start here; tune UP only against logged data, never
// toward the dog (SAFE_MOTION_CBF_SPEC.md §9.2).
#define CANIS_CBF_GAMMA            0.5f

// Loop rates. Control loop = ESP32-side wheel-velocity PI loop (encoder counts
// -> signed PWM). Odom stream = encoder+IMU+ToF frames POSTed to /telemetry
// for the Pi's estimator, AND the /motion (v, omega) poll cadence; the CBF
// spec (§2) requires >= 10 Hz or the state estimate is too stale to certify.
#define CANIS_CONTROL_RATE_HZ      20       // wheel-speed control loop on the ESP32
#define CANIS_ODOM_STREAM_HZ       10       // /telemetry stream + /motion poll rate (spec: >= 10)

// Freshness window on the Pi's (v, omega) command: if the last successful
// /motion poll is older than this, the ESP32 STOPs on its own — a software
// layer ABOVE the Uno's independent 1000 ms MOTION_TIMEOUT_MS watchdog, which
// stays in force unchanged as the final authority.
#define CANIS_MOTION_CMD_TTL_MS    600UL

// Speed caps — hard clamp on ANY commanded u = [v, omega], applied firmware-
// side AFTER the Pi's QP output. Defense in depth: never trust the host to
// have clamped; a buggy/compromised Pi must still be physically slow.
#define CANIS_MAX_LINEAR_MPS       0.35f    // |v| cap, m/s (gentle walking pace)
#define CANIS_MAX_ANGULAR_RPS      1.5f     // |omega| cap, rad/s

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
#if CANIS_WIFI_RETRY_MS < CANIS_WIFI_CHECK_MS
#error "WIFI_RETRY_MS must be >= WIFI_CHECK_MS"
#endif

// --- Mobile / CBF block guards (NEW) ---
#if CANIS_D_MIN_CM >= CANIS_D_MAX_CM
#error "D_MIN_CM must be < D_MAX_CM (comfort band would be empty or inverted)"
#endif
#if CANIS_MOBILE_ENABLED && !CANIS_NEVER_APPROACH
#error "MOBILE builds require NEVER_APPROACH=1 (the CBF layer ADDS to the invariant, it never replaces it)"
#endif
#if CANIS_MOTION_CMD_TTL_MS >= 1000UL
#error "MOTION_CMD_TTL_MS must be < Arduino MOTION_TIMEOUT_MS (1000) so the ESP32 stops FIRST"
#endif
#if CANIS_ODOM_STREAM_HZ < 10
#error "ODOM_STREAM_HZ must be >= 10 (SAFE_MOTION_CBF_SPEC.md section 2: estimator staleness bound)"
#endif
#if CANIS_CONTROL_RATE_HZ < CANIS_ODOM_STREAM_HZ
#error "CONTROL_RATE_HZ must be >= ODOM_STREAM_HZ (wheel loop can't run slower than the command stream)"
#endif

// ======================= Camera architecture A/B/C (NEW) =======================
// docs/MOBILE_UPGRADE_STATUS.md §2/§5b: all three camera architectures are
// implemented behind this ONE switch, so you pick by setting, not rewriting:
//
//   A  CANIS_CAMERA_ONBOARD          robot's-eye; camera attached directly to
//                                    the Pi (picamera2 or USB). Runs
//                                    vision/detect_yolo.py as today.
//   B  CANIS_CAMERA_ESP32CAM_STREAM  robot's-eye; Pi is OFF-BOARD and pulls
//                                    frames from an ESP32-CAM MJPEG/HTTP
//                                    stream. Same detect_yolo.py, different
//                                    frame source. Streaming is flaky by
//                                    nature — SAFETY stays on the onboard ToF;
//                                    only identity/bearing ride the stream.
//   C  CANIS_CAMERA_FIXED_ROOM       FIXED room camera watching the scene;
//                                    YOLO finds the DOG, an ArUco marker ON
//                                    THE ROBOT localizes the robot, and the
//                                    robot->dog distance/bearing are computed
//                                    in the ROOM frame. Runs the dedicated
//                                    vision/detect_fixed.py.
//
// Every mode POSTs through the SAME detection contract
// (docs/INTERFACE_CONTRACT.md §3), so the ESP32 firmware, controller, and
// dashboard are camera-mode-agnostic — this constant exists so the chosen
// architecture is declared in the single source of truth and can be announced
// over /config like every other tunable. Python vision nodes cannot #include
// a header; they mirror this switch via the environment / CLI:
//     CANIS_CAMERA_MODE = "onboard" | "esp32cam_stream" | "fixed_room"
//     (detect_yolo.py --camera-mode ... serves A and B;
//      detect_fixed.py is mode C's dedicated entry point)
// Keep the env string and this #define in agreement per deployment.
//
// Operational note (not compile-fatal): in FIXED_ROOM mode the robot is only
// perceived inside the fixed camera's view — if CANIS_ROAM_ENABLED wanders it
// out of frame, detection (and mobile-mode localization) is simply lost until
// it wanders back. Fine for a single-room study; mind it when enabling roam.
#define CANIS_CAMERA_ONBOARD          0    // A (SAFE/simple DEFAULT — zero new hardware)
#define CANIS_CAMERA_ESP32CAM_STREAM  1    // B
#define CANIS_CAMERA_FIXED_ROOM       2    // C
#define CANIS_CAMERA_MODE             CANIS_CAMERA_ONBOARD

// --- Mode B (esp32cam_stream) tunables ---------------------------------------
// AI-Thinker ESP32-CAM running the stock CameraWebServer sketch serves MJPEG
// at http://<ip>:81/stream. Prefer a static DHCP lease or mDNS name so the
// URL survives reboots. Python mirror: CANIS_ESP32CAM_URL / --stream-url.
#define CANIS_ESP32CAM_STREAM_URL     "http://esp32cam.local:81/stream"
#define CANIS_ESP32CAM_HFOV_DEG       65.0f   // OV2640 stock lens horizontal FOV
#define CANIS_ESP32CAM_STALL_S        5.0f    // no bytes for this long -> tear down + reconnect

// --- Mode C (fixed_room) tunables --------------------------------------------
// Room-frame geometry: the fixed camera sits CANIS_FIXED_CAM_HEIGHT_M above
// the floor, pitched DOWN by CANIS_FIXED_CAM_TILT_DEG (0 = horizontal). Dog
// position = bbox bottom-center ray intersected with the floor plane; robot
// position = marker-center ray intersected with the plane at the marker's
// mounting height. MEASURE height and tilt on the real mount — they multiply
// straight into every distance the barrier layer will trust.
// Python mirror: CANIS_FIXED_CAM_HEIGHT_M / CANIS_FIXED_CAM_TILT_DEG /
// CANIS_CAM_HFOV_DEG / CANIS_ROBOT_MARKER_ID / CANIS_ROBOT_MARKER_HEIGHT_M.
#define CANIS_FIXED_CAM_HFOV_DEG      66.0f   // Pi Cam Module 3 = 66, v2 = 62.2, wide M3 = 102
#define CANIS_FIXED_CAM_HEIGHT_M      2.00f   // lens height above the floor
#define CANIS_FIXED_CAM_TILT_DEG      30.0f   // downward pitch from horizontal
// Robot fiducial: DICT_4X4_50 marker mounted FLAT (facing UP) on the robot's
// top deck, its printed TOP edge (corner 0 -> corner 1) facing the robot's
// FRONT — that convention is how detect_fixed.py recovers the robot's heading.
// MUST NOT collide with the dog's collar-marker ids (legacy detect.py, default 0).
#define CANIS_ROBOT_MARKER_ID         17
#define CANIS_ROBOT_MARKER_SIZE_MM    100.0f  // printed marker edge length
#define CANIS_ROBOT_MARKER_HEIGHT_M   0.18f   // marker plane height above the floor

// --- Camera architecture guards (NEW) ---
#if CANIS_CAMERA_MODE < CANIS_CAMERA_ONBOARD || CANIS_CAMERA_MODE > CANIS_CAMERA_FIXED_ROOM
#error "CANIS_CAMERA_MODE must be CANIS_CAMERA_ONBOARD, CANIS_CAMERA_ESP32CAM_STREAM, or CANIS_CAMERA_FIXED_ROOM"
#endif
#if CANIS_ROBOT_MARKER_ID < 0 || CANIS_ROBOT_MARKER_ID > 49
#error "ROBOT_MARKER_ID must be a valid DICT_4X4_50 id (0..49)"
#endif

#endif  // CANIS_CONFIG_H
