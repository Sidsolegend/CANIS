# CANIS — Technical Writeup

> **Note:** parts of this document were written before the October 2026 rebuild. Since then the PIR motion sensor was removed (the camera covers presence), the front sensor became a JSN-SR04M-2 ultrasonic, and the drive moved to N20 encoder motors. [BUILD_STORY.md](BUILD_STORY.md) lists what changed, and [KNOWN_ISSUES.md](KNOWN_ISSUES.md) has the current state.

CANIS is an autonomous, treat-dispensing behaviour robot for a single dog. This
document describes the system as built: the three-processor architecture, the
real-time control loop, the perception and state-machine stack, the actuation and
safety design, and the behaviour modes. Tunables cited are the shipped defaults from
`optimized/canis_config.h` (the single source of truth); the dashboard mirror is
`optimized/dashboard/canis_config.py`, kept in numeric sync by a parity test and, at
runtime, by the ESP32 POSTing its live config to the Pi so the dashboard shows
firmware truth rather than a hand-copied guess.

## 1. Architecture — three processors, separated by failure domain

Responsibilities are split across three MCUs, each chosen for what it does reliably:

| Tier | Device | Role | Why here |
|---|---|---|---|
| Eyes | Raspberry Pi 5 | Camera, ArUco vision, Flask dashboard | CPU for OpenCV; WiFi-isolated from robot power |
| Brain | ESP32 DevKit V1 | Sensors, arousal state machine, policy, WiFi | Enough GPIO + a second UART; 3.3 V logic |
| Limbs | Arduino Uno | 2× drive motors (L298N), gate servo, buzzer | Dumb, deterministic, hard to crash |

The split is a **safety decomposition**, not just a convenience. The executor (Uno)
can fail safe with a dead brain: its motion watchdog stops the wheels if no fresh
command arrives within `MOTION_TIMEOUT_MS` (1000 ms), so an ESP32 crash, a yanked
UART wire, or a WiFi stall all converge on "robot halts within ~1 s" with nothing
upstream needing to be alive. The Pi is electrically isolated (its own supply, WiFi
only), so its ground never joins the robot's and its ~5 A appetite can't brown out
the logic rail.

## 2. Control loop — a non-blocking superloop

The ESP32 runs a fixed-order superloop (~50 Hz) that never `delay()`s for anything
meaningful:

```
handleInput → readSensors → wifiWatchdog → postConfig → pollCommand
→ serviceVisionTtl → updateArousal → updateState → runStateBehavior
→ serviceMotion → serviceRoam → servicePlace → serviceAck → log → report
```

Every stage is non-blocking so the motion watchdog and vision polling never starve.
HTTP calls to the Pi (`pollCommand`, `reportToPi`) use a short 300 ms timeout and are
skipped entirely while a motion is active, so a slow network can't stretch the 500 ms
motion re-pulse past the Uno's 1000 ms watchdog and stutter the robot near the dog.

A subtle failure mode is handled explicitly: a stalled loop makes one iteration's `dt`
huge, which with a 30/s arousal rise would teleport the dog straight into an
intervention. `dt` is clamped to `DT_MAX_S` (0.2 s) so any single tick can add at most
`(rise − decay) × dt` to arousal.

## 3. Perception

**Ultrasonics (×3).** LEFT / CENTRE / RIGHT (2× HC-SR04 + 1× waterproof SR04M-2),
fired sequentially (a shared trigger cross-talks). Each raw range feeds a zone test
(`≤ ZONE_CM`, 30 cm) and the nearest in-zone sensor gives a coarse **local bearing**.
Each echo passes through a **confirmation filter**: an N-of-N debounce
(`CONFIRM_N = 3` reads at 10 Hz ≈ 300 ms) that must see three agreeing samples before
flipping state, rejecting single-sample noise. PIR runs through the same filter and is
ignored for a 60 s warm-up.

**Vision.** On the Pi, `detect.py` runs ArUco detection at 640×480, gated by a
sharpness test (rejects motion blur) and a frozen-frame guard (fingerprints frames so
a wedged camera can't report a stale empty room). `marker_side()` bins the marker's
horizontal centroid into left / centre / right thirds; with `--directional` the zone
label carries that bearing. A grace period holds the last sighting briefly so a dog
turning his head doesn't read as "left." The Pi exposes this at `/command`; the ESP32
polls it and sets a `visionActive` latch with a **local TTL** (`VISION_TTL_MS`, 4 s)
so that if the Pi or WiFi dies while the latch is set, arousal can't be pinned high
forever at a dog that already left.

**Fusion.** `detectionActive = PIR ∨ zone ∨ vision` drives arousal — with one
important mode-dependent exception (§5): while roaming, only the vision marker counts,
because a moving robot can't tell an ultrasonic wall-return from a dog.

## 4. Arousal state machine

A scalar `arousal ∈ [0,100]` integrates detection: it rises at 30/s while something is
detected and decays at 12/s otherwise (rise must beat decay). A three-state machine —
**CALM → AROUSAL → CORRECTION** — classifies it with **Schmitt-trigger hysteresis**:
separate enter/exit thresholds (CORRECTION 70↑ / 60↓, AROUSAL 20↑ / 12↓) create a
dead-band, so a dog hovering at a boundary can't chatter the state (and re-fire
interventions) every frame. AROUSAL is deliberately watch-only — the system observes
before it ever acts, and a transient 1 s blip peaks below the AROUSAL threshold.

## 5. Behaviour modes

Three runtime modes toggle live over serial (`g` / `p` / `b`). A single hard invariant,
`CANIS_NEVER_APPROACH`, runs through all of them: **the robot never translates toward
the dog** — the legacy level-2 forward "approach" is compiled out, guard correction is
buzzer-only, and any dog-directed motion is a retreat.

- **GUARD** (stationary sentry). The full arousal machine runs; CORRECTION fires an
  800 Hz disengage buzzer, and the CALM transition on the dog leaving dispenses the
  reward. Sustained-calm rewards also apply here. Correction, leave-reward, and
  calm-reward are all gated to GUARD only, so the other modes don't inherit them.

- **PLAY** (enrichment / roam). `serviceRoam()` reactively wanders using raw ultrasonic
  ranges (drive FORWARD unless anything is within `ROAM_CLEAR_CM` = 35 cm, else pivot
  toward the side with more room; `−1`/no-echo = clear). When Shifu's marker appears in
  the forward camera it dispenses a treat and **flees** via `fleeDir()` — which reads
  the *camera* bearing only (the dog is in front at trigger time): front-left → peel
  right, front-right → peel left, centre → back straight off (which also keeps him in
  frame during the retreat). Ultrasonics are explicitly *not* trusted for the dog's
  bearing. This doubles as counter-conditioning: moving robot + treat = positive.

- **PLACE** (place-training). `servicePlace()`: point the robot at the dog's mat; while
  his marker is held in view he must settle for `PLACE_SETTLE_MS` (6 s) before the
  first treat, then earns one every `PLACE_REWARD_MS` (45 s) for *staying*. Leaving
  resets the timer — he is never rewarded for leaving. No buzzer, no motion.

Motion in every mode is **bounded and re-pulsed**: `startMotion(cmd, dur)` sends a
command the Uno executes for at most `dur`, and the ESP32 re-sends it every
`MOVE_PULSE_MS` (500 ms) *only while it still means it*, so motion cannot latch — the
brain holds a dead-man's switch and the Uno watchdog is the backstop.

## 6. Actuation — the treat dispenser

An SG90 servo swings a printed **flap gate**: a funnel feeds a one-treat throat, the
flap holds that treat over a drop hole (CLOSED = 80°), and the servo swings it clear
(OPEN = 0°) to release exactly one. Two integrity mechanisms sit on top:

- **Delivery accounting.** Each dispense carries an incrementing **correlation id**;
  the Uno echoes it on ACK (delivered) / JAM (gate cycled, nothing fell). The ESP32
  counts a reward only on a matching ACK, so a treat is counted *delivered*, not
  *requested* — and a local (button) dispense replies with sentinel id 0, which the
  ESP32 never miscredits to a pending reward. An optional beam-break sensor upgrades
  ACK from "gate mechanically cycled" to "a treat physically fell."
- **Fault policy.** After `JAM_FAULT_THRESHOLD` (2) consecutive jams the dispenser is
  marked faulted and treat requests are suppressed — an unpaired marker tone with no
  treat behind it erodes the conditioning, so a broken dispenser must go silent, not
  keep beeping. A manual `d` clears it after a refill.

A **treat governor** bounds all of this regardless of mode: a 20 s cooldown between
any two treats and a 30-treat session cap, so no behaviour mode can overfeed the dog.

## 7. Communications

- **Pi ↔ ESP32:** HTTP over WiFi. Mutating routes require a shared-secret token
  (mirrored in firmware) so spoofed traffic is rejected; a non-blocking WiFi watchdog
  reconnects without busy-waiting; a POST backoff pauses traffic if the Pi is dead.
- **ESP32 ↔ Arduino:** a 9600-baud serial link. The command direction (3.3→5 V) is
  safe as-is; the ACK return line (5→3.3 V) **must** pass a 1 k/2 k divider or level
  shifter. If the return line is unwired, `CANIS_USE_DISPENSE_ACK = 0` reverts to
  count-on-send. The command vocabulary is six words (`DISPENSE`, `FORWARD`, `BACKWARD`,
  `LEFT`, `RIGHT`, `STOP`); unknown/corrupted lines are ignored, and the safe response
  to a garbled motion command is to let the watchdog stop the wheels.

## 8. Safety & fail-safes (layered)

- **Executor watchdog** — Uno stops the wheels ≤ 1 s after commands cease (any upstream
  failure). Uses elapsed-time-by-subtraction so `millis()` wrap can't defeat it.
- **Never-approach invariant** — the robot cannot drive toward the dog in any mode.
- **Bounded, re-pulsed, non-latching motion** — intent must be continuous to persist.
- **Exposure gate** — a compile/runtime welfare level (defaults to no motion), changed
  only via a two-step arm→confirm with auto-expiry so a stray keypress can't enable
  motion.
- **Vision-latch TTL + PIR warm-up** — no phantom "dog present" from a dead link or a
  settling sensor.
- **Observe-only build** — a compile flag turns the robot into a pure sensor (full
  pipeline + dashboard, all actuators gated) for measuring false-positive rates with
  zero risk of a stray cue reaching the dog.
- **Treat cooldown + session cap + jam-fault silence** — bounded, self-quieting output.

## 9. Commissioning & tooling

A single serial command `c` runs a guided self-test that sequences the motor,
dispenser, and sensor checks — and turns two "redo the assembly" mistakes into
persisted software fixes: a reversed wheel flips and *saves* an invert flag on the
Uno's EEPROM (no re-wiring), and a mis-seated flap is corrected with a saved servo
trim. `detect.py --selftest` is the camera analogue: it grabs frames, looks for the
dog marker, and prints a pass/fail with a likelihood-ranked cause on failure, exiting
with a process code so it can be scripted. A pure-Python mirror of the decision logic
is unit-tested off-hardware (88 tests) so state-machine and dispenser behaviour can be
pinned without a serial port.

## 10. Limitations

The honest ceiling of this sensor suite: the dog is identified only by an ArUco marker
in the **forward** camera, so CANIS reacts when he comes into view rather than tracking
him around the house; there is no localisation or mapping, so PLAY is reactive wander,
not systematic coverage; and there is no cliff/bump/rear sensor, so backing up is blind
and roaming is single-floor by design. Behaviours the robot cannot *detect* — random
scavenging, toy destruction, barking (no microphone) — it cannot treat-train; those
remain a management problem, addressed only indirectly by a less-bored dog. These
limits are stated in the design rather than papered over, because a treat-based system
can only reinforce what it can observe.
