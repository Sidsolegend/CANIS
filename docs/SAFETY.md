# CANIS - Safety & Fail-Safe Reference

Every fail-safe in the system, enumerated: what triggers it, what it does, and
how to test it. The design intent throughout: **any single failure must leave
the robot stopped and quiet near the dog.** Shifu is afraid of moving objects;
a runaway or a stuttering robot is the worst-case failure, so motion is treated
as a rationed hazard, not a free actuator.

Conventions: "orig" = original tree; `[OPT-n]` = hardening documented in
`OPTIMIZATIONS.md`. Test procedures assume the bench setup of
`hardware/TESTING.md`; run them with wheels off the ground first, and never
test failure modes with the dog in the room.

---

## Layer 0 - Design-level (always in force)

### 0.1 Reward-on-leaving (never punish entering)
- **Trigger:** state falls back to CALM from AROUSAL/CORRECTION.
- **Response:** marker + treat. Entering a zone is never met with anything
  aversive beyond the 800 Hz cue at CORRECTION.
- **Why safety:** for a fearful dog, punishment pairs the robot with fear; the
  entire intervention stays appetitive.
- **Test:** TESTING.md Test 7 step 2 (walk in → CORRECTION; leave → reward).

### 0.2 EXPOSURE_LEVEL gate - the welfare knob
- **Trigger:** every correction cycle checks it before any motion.
- **Response:** 0 = buzzer only (**default at boot**, `CANIS_EXPOSURE_DEFAULT`);
  1 = orient/pivot only; 2 = orient + 400 ms gentle approach. Level persists
  only until reboot - a power cycle always returns to 0 (fail-quiet).
- **Test:** at level 0, force CORRECTION (`t` key ×3): buzzer only, motors
  never move. Press `m` to 1: pivots only. Confirm the dashboard shows the level.

### 0.3 Level-1 never-approach invariant `[OPT-8]`
- **Trigger:** correction motion requested at exposure < 2 with a `FORWARD`
  bearing (CENTRE sensor nearest, or `zoneToDir` default).
- **Response:** bearing rewritten to an in-place pivot; the robot can never
  close distance below level 2.
- **Test:** unit (`test_level1_forward_is_rewritten_to_pivot`); on hardware:
  level 1, trigger CORRECTION with only the CENTRE ultrasonic covered - robot
  must pivot, not drive forward.

### 0.4 AROUSAL is watch-only
- **Trigger:** arousal in [20, 70).
- **Response:** nothing. The system observes before it ever acts; transient
  detections cannot reach an intervention (a 1 s blip peaks < 20 - unit-tested).

---

## Layer 1 - Motion fail-safes

### 1.1 Arduino motion watchdog (the backstop)
- **Trigger:** no fresh motion command for `MOTION_TIMEOUT_MS` (1000 ms) while
  motors are running (`Treat_Dispenser_CANIS.ino` loop).
- **Response:** `stopMotors()` - unconditional, executor-side, requires nothing
  else alive. Covers: ESP32 crash/reset, UART wire loose, WiFi-storm stall,
  brain firmware bug - all converge on "stopped within ~1 s".
- **Test:** TESTING.md Test 1 ("each command drives ~1 s then auto-stops");
  live: start `f` (test FORWARD) and pull the GPIO13 jumper mid-motion - wheels
  must stop within ~1 s. **Re-run after any firmware change to either board.**

### 1.2 Bounded motion + re-pulse (intent must be continuous)
- **Trigger:** all correction motions are duration-bounded (`ORIENT_MS` 700 ms,
  `APPROACH_MS` 400 ms); the ESP32 re-sends the command every 500 ms *only
  while it still means it* (`serviceMotion`).
- **Response:** motion cannot be latched; ending intent ends motion ≤ 1 s later
  even if the STOP is lost.
- **Test:** watch Serial: an orient prints one start + pulses, then STOP at
  ~700 ms.

### 1.3 Immediate STOP on disengagement
- **Trigger:** state leaves CORRECTION (`updateState`).
- **Response:** explicit `STOP` sent at once - the dog that disengaged is not
  chased for even the watchdog's remaining second.
- **Test:** level 1, hold CORRECTION with a hand in front of a sensor, remove
  it and `r`-reset: motion must stop instantly with the state change.

### 1.4 Watchdog-feed integrity during HTTP `[OPT-10]`
- **Trigger:** any active motion.
- **Response:** `reportToPi`/`pollCommand` (each can block ~300 ms) are
  deferred until the motion ends, so the 500 ms pulse cadence can never stretch
  past the 1000 ms watchdog and stutter the robot mid-orient.
- **Test:** level 1 with the Pi **powered off** (worst-case HTTP latency):
  orient pulses must stay smooth; post-motion, the report resumes.

### 1.5 Exposure-0 cold boot
- **Trigger:** any reboot/brownout of the ESP32.
- **Response:** `EXPOSURE_LEVEL` returns to 0; a recovering system is always
  motionless until a human raises it again.
- **Test:** raise to 1, reboot, confirm "Exposure level: 0" on boot banner.

---

## Layer 2 - Perception fail-safes

### 2.1 Sensor confirmation filter
- **Trigger:** every raw PIR/ultrasonic read.
- **Response:** 3 consecutive agreeing samples (~300 ms) to flip a confirmed
  state - curtains, fans, stray echoes don't count.
- **Test:** TESTING.md Test 3 (RAW vs CONFIRMED columns).

### 2.2 PIR warm-up mask
- **Trigger:** first 60 s after boot.
- **Response:** PIR ignored (hardware settles); calm rewards also held for the
  window so boot noise can't spend treats.
- **Test:** boot and wave at the PIR < 60 s: no MOTION print.

### 2.3 dt clamp `[OPT-4]`
- **Trigger:** any loop tick with dt > 0.2 s (WiFi/HTTP stall, scheduler hiccup).
- **Response:** integration capped - a stall cannot teleport arousal into a
  phantom CORRECTION.
- **Test:** unit (`test_dt_clamp_prevents_single_tick_jump_to_correction`).

### 2.4 Vision-latch TTL `[OPT-9]`
- **Trigger:** `visionActive` set but no successful `/command` poll refresh for
  4 s (Pi dead, router reboot, WiFi drop).
- **Response:** latch cleared locally; arousal decays; no permanent phantom
  CORRECTION at an empty zone.
- **Test:** unit (`test_vision_latch_expires_when_polls_stop`); on hardware:
  trigger a vision detection (`detect.py --test`), then kill the dashboard - ESP32 must print `latch expired` ≤ ~4 s later and settle to CALM.

### 2.5 Marker-loss grace `[OPT-12]` + frozen-frame guard `[OPT-13]`
- **Trigger:** grace: marker unseen ≤ 1.5 s after a sighting. frozen: 30
  identical frame fingerprints.
- **Response:** grace holds the zone (no arousal saw-tooth / false
  leaving-reward on a head-turn); frozen forces a camera reopen instead of
  silently watching a stale image.
- **Test:** unit (`test_vision_logic.py`); live: cover the marker ~1 s - the
  posted zone must not flap.

### 2.6 Command TTL on the Pi
- **Trigger:** no fresh `/detect` POST for 3 s.
- **Response:** `/command` returns `NONE`; a crashed vision script cannot hold
  a detection alive.
- **Test:** `detect.py --test` once; `curl /command` shows the zone, then
  `NONE` after 3 s.

---

## Layer 3 - Distance barrier (CBF certificate) - MOBILE ONLY

**Scope:** everything in this layer is *inert in the shipped configuration.* It
arms only when `CANIS_MOBILE_ENABLED = 1` (SAFE DEFAULT `0`, `canis_config.h`);
in the stationary build the Pi controller emits no drive command and this layer
has nothing to gate. It **ADDS** to the stack - it removes nothing above it, and
it is compile-guarded to require `CANIS_NEVER_APPROACH = 1` (a `#error` fails the
mobile build otherwise), so it can only ever add conservatism.

The discrete never-approach rule (§0.3, `[OPT-8]`) forbids *forward motion below
exposure 2*. This layer generalizes it into a continuous, measured guarantee: the
robot→dog distance `d_rd` can never drop below the dog's adaptive comfort margin
`D_comfort(s) ∈ [D_MIN, D_MAX]` (`CANIS_D_MIN_CM`=60, `CANIS_D_MAX_CM`=200) for
**any** commanded input. Math + rationale: `docs/SAFE_MOTION_CBF_SPEC.md`; wire
formats: `docs/INTERFACE_CONTRACT.md`; controller: `control/`.

### 3.1 CBF safety-filter clamp (the certificate)
- **Trigger:** every control tick (≥ 10 Hz, `CANIS_ODOM_STREAM_HZ`) while mobile.
  The Pi estimator fuses encoder+IMU odometry with the VL53LX ToF range - **gated
  by the YOLO bearing so it ranges the DOG, not a wall** - into `d_rd`, `β`; the
  barrier QP filters the nominal command `u_nom = [v, ω]`.
- **Response:** the closed-form QP projects `u_nom` onto the half-space
  `v·cosβ ≤ γ·h`, `h = d_rd − D_comfort(s)`, `γ = CANIS_CBF_GAMMA` (0.5). At or
  inside the boundary (`h ≤ 0`) only distance-holding/opening commands survive - the forward component clamps to `v ≤ 0`. Heading/pivot (ω) is *not*
  distance-reducing and passes untouched (*facing ≠ approaching*). Independently,
  a firmware clamp (INTERFACE_CONTRACT §2.2) forces `v ≤ 0` whenever the dog is
  detected and `tof_m ≤ D_MIN`, so the guarantee never rests on the Pi alone.
- **Invariant (state + test):** **no single sensor or compute failure can produce
  a distance-reducing command.** Asserted three independent ways by
  `control/motion_controller.py --selftest`: a 5000-sample QP fuzz proving
  `h ≤ 0 ⇒ v*·cosβ ≤ 0`; a closed-loop sim where a relentless `v = +0.3` nominal
  settles at the boundary with zero distance-reducing commands (min
  `h_true = +0.027 m`); and dropout scenarios (detection loss → `v ≤ 0` after the
  hold; telemetry loss → STOP).
- **Bench test (wheels off the ground, no dog in the room):**
  1. **Spoof ToF below comfort:** inject a `tof_m` below `D_MIN` (60 cm) with a
     dog bearing present and command a full-forward nominal. *Pass:* the served
     `MOVE` line has `v ≤ 0` (forward clamped), and the firmware clamp holds even
     if the Pi's QP output is bypassed.
  2. **Pull the ToF:** disconnect it / force the `-1.0` sentinel. *Pass:* `d_rd`
     goes unknown ⇒ `h` is evaluated at the boundary (worst-case β) ⇒ no
     distance-reducing motion - i.e. it **falls back to the exposure gate**,
     exactly as if below exposure 2.

### 3.2 Uncertainty shrinks the margin, never grows it
- **Trigger:** every barrier evaluation.
- **Response:** the barrier is evaluated on `d_rd − min(σ, 30 cm)` (σ = estimator
  std-dev), so a *less* certain estimate makes `h` **smaller** (more
  conservative), never larger. Ahead of the QP: stale telemetry → STOP; exposure
  0 → STOP; exposure 1 → nominal forward forced to 0 before the QP even runs.

### 3.3 Degradation is one-way toward stationary
- **Trigger:** loss of any barrier input (ToF, vision bearing, odometry) or of the
  command/telemetry channel.
- **Response:** the mobile stack can only ever fall **back** onto the pre-existing
  stack - exposure gate (§0.2), never-approach (§0.3), the Uno 1 s watchdog
  (§1.1), power-cycle→exposure-0 (§1.5). The CBF never overrides a stop.
- **Test:** kill the `/detect` stream mid-run → after the 1.5 s grace, commanded
  `v ≤ 0`; kill `/telemetry` or `/motion` → the ESP32 STOPs within
  `CANIS_MOTION_CMD_TTL_MS` (600 ms), the Uno watchdog backstops at 1 s.

### 3.4 Camera-architecture failure modes (A / B / C)
The barrier's **safety** signal is always the ONBOARD ToF; the camera only
supplies dog IDENTITY/bearing (which ToF return is "the dog"). This split is
deliberate so the perception choice can never weaken the certificate - it
resolves `SAFE_MOTION_CBF_SPEC.md` §9 open decision #1: identity sensor = **YOLO**
(`detect_yolo.py`), the barrier keys off **ToF distance**, so the two are
independent. Camera architecture is one switch, `CANIS_CAMERA_MODE`:

| Mode | Camera path | New failure mode | Fail-safe |
|---|---|---|---|
| **A** `onboard` | camera on the Pi (CSI/USB) | camera read fail / frozen frame | `[OPT-6b]`/`[OPT-13]` reopen; vision goes silent → grace → no distance-reducing motion |
| **B** `esp32cam_stream` | ESP32-CAM MJPEG → off-board Pi over **WiFi** | stream drop / stall / brownout / latency | a stalled stream is treated as a failed camera read (reconnect); **safety stays on the onboard ToF**, only identity/bearing rides the stream; if the motion command then goes stale → **STOP** (motion-cmd TTL watchdog) |
| **C** `fixed_room` | fixed room camera → off-board Pi; a DICT_4X4_50 marker (`CANIS_ROBOT_MARKER_ID`=17) localizes the robot, YOLO finds the dog | **WiFi** stream drop; robot roams out of the fixed camera's view (marker lost) | marker lost → robot pose unknown → `distance_m` null → barrier evaluated at the boundary → no distance-reducing motion; stale motion cmd → **STOP** |

In modes B and C the Pi is off-board, so the drive command itself rides **WiFi** - a new dependency the stationary/onboard build does not have. A dropped link is
caught fastest-first by the watchdog chain (INTERFACE_CONTRACT §2.3): Pi serves
`STOP` > ESP32 STOPs at 600 ms > Uno cuts PWM at 1 s. **Modes B and C add a
WiFi/stream failure mode; its fail-safe is STOP.**

---

## Layer 4 - Reward-system fail-safes

### 4.1 Cooldown + session cap
- **Trigger:** any reward request < 20 s after the last, or beyond 30/session.
- **Response:** held (logged). Bouncing at a zone edge can't rapid-fire treats;
  the hopper can't empty; the dog can't be overfed by a bug.
- **Test:** unit (`test_reward_cooldown_blocks_back_to_back`,
  `test_reward_session_cap`); config guard: `CALM_REWARD_MS >=
  REWARD_COOLDOWN_MS` fails the **build** if violated (`canis_config.h #error`).

### 4.2 Motors stop before every dispense
- **Trigger:** any DISPENSE.
- **Response:** `stopMotors()` first - rewards are delivered by a stationary
  robot (welfare) and Timer2 is freed for the marker tone (Uno tone/PWM
  conflict, documented in the sketch).
- **Test:** send `FORWARD` then immediately `DISPENSE` on the Uno monitor:
  wheels stop before the beep.

### 4.3 Delivery verification: ACK `[OPT-7]` + jam detection `[OPT-11]`
- **Trigger:** gate cycled but no beam-break within 600 ms (jam / empty hopper /
  wedged treat).
- **Response:** one retry with a longer gate hold; then `JAM` reply - the treat
  is never counted, and 2 consecutive JAMs **fault** the dispenser: all further
  auto-rewards are suppressed (a marker with no treat erodes the conditioned
  signal - going quiet is the correct failure mode). Dashboard shows a red
  fault banner; operator fixes the hopper and presses `d` to clear.
- **Test:** unit (`DispenserHealth` tests); on hardware (sensor installed):
  run a dispense with an empty hopper - expect retry, `JAM`, fault after the
  second, and no further marker beeps until `d`.

### 4.4 ACK timeout
- **Trigger:** no ACK/JAM within 2.5 s of a DISPENSE (return wire fault).
- **Response:** treat not counted; slot freed; logged. Counts stay honest.
- **Test:** disconnect the pin-11 return line and trigger a reward: expect the
  timeout log, `reward_count` unchanged.

---

## Layer 5 - Network & data fail-safes

- **5.1 Dead-Pi back-off:** failed POST/poll pauses HTTP 5 s (rollover-safe
  comparison), keeping the 20 ms control loop snappy with the Pi down.
  *Test:* kill the dashboard; loop logs keep 1 Hz cadence.
- **5.2 WiFi reconnect watchdog `[OPT-5]`:** non-blocking `WiFi.reconnect()`
  every 5 s when down; robot is fully functional offline (sensors, FSM,
  dispenser all local). *Test:* toggle the AP; link self-heals, no loop stall.
- **5.3 Dashboard auth + validation `[OPT-2]`:** LAN attacker without the token
  cannot inject `/detect` (steer the robot) or fake `/ingest`; all numerics
  bounded/NaN-safe. *Test:* `curl` without token → 401.
- **5.4 Simulator honesty:** with the ESP32 silent > 5 s the UI switches to
  demo data and **says so** in the footer - stale data is never presented as
  live. *Test:* unplug ESP32; footer flips to "demo data - simulator".
- **5.5 Event-log durability `[OPT-14]`:** append-per-write JSONL; reader
  tolerates a torn last line (power cut). *Test:* unit
  (`test_read_events_survives_truncated_last_line`).

---

## Layer 6 - Electrical (see `hardware/WIRING.md`)

- **6.1 Separate motor power:** L298N motor rail from its own pack, never a
  logic regulator; grounds common on the robot side. *Test:* TESTING.md Test 6.
- **6.2 5 V→3.3 V protection:** every ultrasonic ECHO through 1 kΩ (upgrading GPIO 34/35 to
  1 k/2 k dividers would be better); ACK return line **must** be divided/shifted.
- **6.3 Buck dial-in rule:** LM2596 set to 5.0–5.1 V with a meter **before**
  anything connects to its output (an unset buck can sit > 10 V).
- **6.4 Brownout-under-stall test (NEW - run before untethered sessions):**
  wheels on carpet, command FORWARD and physically hold the chassis (motor
  stall ≈ worst-case current) for 3 s. Pass: neither MCU resets (watch both
  serial banners), buck output stays ≥ 4.8 V. Fail ⇒ move to the 6×AA/2S plan
  before any session with the dog.
- **6.5 Motor noise caps:** 0.1 µF across each motor's terminals - verify
  installed before untethered runs (brush noise can reset MCUs = uncontrolled
  behavior near the dog).

---

## Operational rules (human layer)

1. The robot never runs unattended around the dog; sessions are supervised and
   welfare stop-criteria apply (`study/STUDY_PROTOCOL.md` §8).
2. Exposure level rises one step at a time, only on the protocol's criteria,
   never mid-session.
3. Any unexplained behavior = power down first, diagnose second.
4. After **any** firmware change: re-run watchdog test 1.1 and the level-0
   no-motion test 0.2 before the next dog session.
