# CANIS — Safety & Fail-Safe Reference

Every fail-safe in the system, enumerated: what triggers it, what it does, and
how to test it. The design intent throughout: **any single failure must leave
the robot stopped and quiet near the dog.** Shifu is afraid of moving objects;
a runaway or a stuttering robot is the worst-case failure, so motion is treated
as a rationed hazard, not a free actuator.

Conventions: "orig" = original tree; `[OPT-n]` = hardening documented in
`optimized/OPTIMIZATIONS.md`. Test procedures assume the bench setup of
`hardware/TESTING.md`; run them with wheels off the ground first, and never
test failure modes with the dog in the room.

---

## Layer 0 — Design-level (always in force)

### 0.1 Reward-on-leaving (never punish entering)
- **Trigger:** state falls back to CALM from AROUSAL/CORRECTION.
- **Response:** marker + treat. Entering a zone is never met with anything
  aversive beyond the 800 Hz cue at CORRECTION.
- **Why safety:** for a fearful dog, punishment pairs the robot with fear; the
  entire intervention stays appetitive.
- **Test:** TESTING.md Test 7 step 2 (walk in → CORRECTION; leave → reward).

### 0.2 EXPOSURE_LEVEL gate — the welfare knob
- **Trigger:** every correction cycle checks it before any motion.
- **Response:** 0 = buzzer only (**default at boot**, `CANIS_EXPOSURE_DEFAULT`);
  1 = orient/pivot only; 2 = orient + 400 ms gentle approach. Level persists
  only until reboot — a power cycle always returns to 0 (fail-quiet).
- **Test:** at level 0, force CORRECTION (`t` key ×3): buzzer only, motors
  never move. Press `m` to 1: pivots only. Confirm the dashboard shows the level.

### 0.3 Level-1 never-approach invariant `[OPT-8]`
- **Trigger:** correction motion requested at exposure < 2 with a `FORWARD`
  bearing (CENTRE sensor nearest, or `zoneToDir` default).
- **Response:** bearing rewritten to an in-place pivot; the robot can never
  close distance below level 2.
- **Test:** unit (`test_level1_forward_is_rewritten_to_pivot`); on hardware:
  level 1, trigger CORRECTION with only the CENTRE ultrasonic covered — robot
  must pivot, not drive forward.

### 0.4 AROUSAL is watch-only
- **Trigger:** arousal in [20, 70).
- **Response:** nothing. The system observes before it ever acts; transient
  detections cannot reach an intervention (a 1 s blip peaks < 20 — unit-tested).

---

## Layer 1 — Motion fail-safes

### 1.1 Arduino motion watchdog (the backstop)
- **Trigger:** no fresh motion command for `MOTION_TIMEOUT_MS` (1000 ms) while
  motors are running (`Treat_Dispenser_CANIS.ino` loop).
- **Response:** `stopMotors()` — unconditional, executor-side, requires nothing
  else alive. Covers: ESP32 crash/reset, UART wire loose, WiFi-storm stall,
  brain firmware bug — all converge on "stopped within ~1 s".
- **Test:** TESTING.md Test 1 ("each command drives ~1 s then auto-stops");
  live: start `f` (test FORWARD) and pull the GPIO13 jumper mid-motion — wheels
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
- **Response:** explicit `STOP` sent at once — the dog that disengaged is not
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

## Layer 2 — Perception fail-safes

### 2.1 Sensor confirmation filter
- **Trigger:** every raw PIR/ultrasonic read.
- **Response:** 3 consecutive agreeing samples (~300 ms) to flip a confirmed
  state — curtains, fans, stray echoes don't count.
- **Test:** TESTING.md Test 3 (RAW vs CONFIRMED columns).

### 2.2 PIR warm-up mask
- **Trigger:** first 60 s after boot.
- **Response:** PIR ignored (hardware settles); calm rewards also held for the
  window so boot noise can't spend treats.
- **Test:** boot and wave at the PIR < 60 s: no MOTION print.

### 2.3 dt clamp `[OPT-4]`
- **Trigger:** any loop tick with dt > 0.2 s (WiFi/HTTP stall, scheduler hiccup).
- **Response:** integration capped — a stall cannot teleport arousal into a
  phantom CORRECTION.
- **Test:** unit (`test_dt_clamp_prevents_single_tick_jump_to_correction`).

### 2.4 Vision-latch TTL `[OPT-9]`
- **Trigger:** `visionActive` set but no successful `/command` poll refresh for
  4 s (Pi dead, router reboot, WiFi drop).
- **Response:** latch cleared locally; arousal decays; no permanent phantom
  CORRECTION at an empty zone.
- **Test:** unit (`test_vision_latch_expires_when_polls_stop`); on hardware:
  trigger a vision detection (`detect.py --test`), then kill the dashboard —
  ESP32 must print `latch expired` ≤ ~4 s later and settle to CALM.

### 2.5 Marker-loss grace `[OPT-12]` + frozen-frame guard `[OPT-13]`
- **Trigger:** grace: marker unseen ≤ 1.5 s after a sighting. frozen: 30
  identical frame fingerprints.
- **Response:** grace holds the zone (no arousal saw-tooth / false
  leaving-reward on a head-turn); frozen forces a camera reopen instead of
  silently watching a stale image.
- **Test:** unit (`test_vision_logic.py`); live: cover the marker ~1 s — the
  posted zone must not flap.

### 2.6 Command TTL on the Pi
- **Trigger:** no fresh `/detect` POST for 3 s.
- **Response:** `/command` returns `NONE`; a crashed vision script cannot hold
  a detection alive.
- **Test:** `detect.py --test` once; `curl /command` shows the zone, then
  `NONE` after 3 s.

---

## Layer 3 — Reward-system fail-safes

### 3.1 Cooldown + session cap
- **Trigger:** any reward request < 20 s after the last, or beyond 30/session.
- **Response:** held (logged). Bouncing at a zone edge can't rapid-fire treats;
  the hopper can't empty; the dog can't be overfed by a bug.
- **Test:** unit (`test_reward_cooldown_blocks_back_to_back`,
  `test_reward_session_cap`); config guard: `CALM_REWARD_MS >=
  REWARD_COOLDOWN_MS` fails the **build** if violated (`canis_config.h #error`).

### 3.2 Motors stop before every dispense
- **Trigger:** any DISPENSE.
- **Response:** `stopMotors()` first — rewards are delivered by a stationary
  robot (welfare) and Timer2 is freed for the marker tone (Uno tone/PWM
  conflict, documented in the sketch).
- **Test:** send `FORWARD` then immediately `DISPENSE` on the Uno monitor:
  wheels stop before the beep.

### 3.3 Delivery verification: ACK `[OPT-7]` + jam detection `[OPT-11]`
- **Trigger:** gate cycled but no beam-break within 600 ms (jam / empty hopper /
  wedged treat).
- **Response:** one retry with a longer gate hold; then `JAM` reply — the treat
  is never counted, and 2 consecutive JAMs **fault** the dispenser: all further
  auto-rewards are suppressed (a marker with no treat erodes the conditioned
  signal — going quiet is the correct failure mode). Dashboard shows a red
  fault banner; operator fixes the hopper and presses `d` to clear.
- **Test:** unit (`DispenserHealth` tests); on hardware (sensor installed):
  run a dispense with an empty hopper — expect retry, `JAM`, fault after the
  second, and no further marker beeps until `d`.

### 3.4 ACK timeout
- **Trigger:** no ACK/JAM within 2.5 s of a DISPENSE (return wire fault).
- **Response:** treat not counted; slot freed; logged. Counts stay honest.
- **Test:** disconnect the pin-11 return line and trigger a reward: expect the
  timeout log, `reward_count` unchanged.

---

## Layer 4 — Network & data fail-safes

- **4.1 Dead-Pi back-off:** failed POST/poll pauses HTTP 5 s (rollover-safe
  comparison), keeping the 20 ms control loop snappy with the Pi down.
  *Test:* kill the dashboard; loop logs keep 1 Hz cadence.
- **4.2 WiFi reconnect watchdog `[OPT-5]`:** non-blocking `WiFi.reconnect()`
  every 5 s when down; robot is fully functional offline (sensors, FSM,
  dispenser all local). *Test:* toggle the AP; link self-heals, no loop stall.
- **4.3 Dashboard auth + validation `[OPT-2]`:** LAN attacker without the token
  cannot inject `/detect` (steer the robot) or fake `/ingest`; all numerics
  bounded/NaN-safe. *Test:* `curl` without token → 401.
- **4.4 Simulator honesty:** with the ESP32 silent > 5 s the UI switches to
  demo data and **says so** in the footer — stale data is never presented as
  live. *Test:* unplug ESP32; footer flips to "demo data — simulator".
- **4.5 Event-log durability `[OPT-14]`:** append-per-write JSONL; reader
  tolerates a torn last line (power cut). *Test:* unit
  (`test_read_events_survives_truncated_last_line`).

---

## Layer 5 — Electrical (see `hardware/WIRING.md`)

- **5.1 Separate motor power:** L298N motor rail from its own pack, never a
  logic regulator; grounds common on the robot side. *Test:* TESTING.md Test 6.
- **5.2 5 V→3.3 V protection:** every ultrasonic ECHO through 1 kΩ (review
  recommends upgrading GPIO 34/35 to 1 k/2 k dividers —
  `ENGINEERING_REVIEW.md` §7.1); ACK return line **must** be divided/shifted.
- **5.3 Buck dial-in rule:** LM2596 set to 5.0–5.1 V with a meter **before**
  anything connects to its output (an unset buck can sit > 10 V).
- **5.4 Brownout-under-stall test (NEW — run before untethered sessions):**
  wheels on carpet, command FORWARD and physically hold the chassis (motor
  stall ≈ worst-case current) for 3 s. Pass: neither MCU resets (watch both
  serial banners), buck output stays ≥ 4.8 V. Fail ⇒ move to the 6×AA/2S plan
  before any session with the dog (`ENGINEERING_REVIEW.md` §7.2).
- **5.5 Motor noise caps:** 0.1 µF across each motor's terminals — verify
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
