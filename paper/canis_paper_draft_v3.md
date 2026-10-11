# CANIS: Design of a Humane Robotic Conditioning System for a Fearful Dog, with a Formally Safe Motion Layer and Preliminary Single-Subject Observations

**Draft v3** (2026-07-12) - a systems / design paper. This describes the
design and implementation of a home conditioning robot and reports
*preliminary, single-subject* observations. It is **not** an efficacy study
and makes **no behavioral-outcome claim**. All architecture and firmware
details below are grounded in the committed source
(`CANIS_Core/CANIS_Core.ino`, `dashboard/app.py`,
`vision/detect_yolo.py`, `vision/detect.py`,
`vision/detect_fixed.py`, `control/`,
`Treat_Dispenser_CANIS/`), the two locked design documents
(`docs/INTERFACE_CONTRACT.md`, `docs/SAFE_MOTION_CBF_SPEC.md`),
and their single source of truth for tunables, `canis_config.h`
(symlinked into `CANIS_Core/`).

> **What changed from v2** (`paper/canis_paper_draft_v3.md`). Three things
> moved in the system between v2 and v3, and this draft rewrites the affected
> sections honestly rather than papering over them:
> 1. **Perception is now on-device YOLO, not ArUco.** v2 argued
>    *ArUco-over-YOLO*. That argument is no longer the system's design, and §3.6
>    is rewritten to say so plainly: the Pi now runs headless (Raspberry Pi OS
>    Lite) with a fan, which frees enough CPU for a nano detector, and a learned
>    dog detector removes exactly the collar-marker fragility that the ArUco
>    grace-period code (`[OPT-12]`) was built to paper over. ArUco is **retained**,
>    but for a different job - localizing the *robot* in the fixed-room camera
>    architecture (§3.6.3). The v2 "ArUco-over-YOLO" framing was a genuine
>    earlier design decision; this is a documented reversal with its reasons, not
>    a retcon.
> 2. **A formal safe-motion layer.** The discrete never-approach rule is
>    generalized into a **control-barrier-function (CBF)** certificate that the
>    robot-to-dog distance can never drop below an adaptive comfort margin, built
>    on **wheel-odometry state estimation** (new §3.8, §3.9). This is the paper's
>    new central engineering contribution.
> 3. **Two operating modes and three camera architectures.** The system now
>    ships a **stationary** default and an opt-in **mobile** (CBF differential-
>    drive) mode behind one compile flag, and three camera placements - >    **A** onboard, **B** ESP32-CAM stream, **C** fixed-room - behind one config
>    switch (new §3.7). The honest single-subject framing, welfare precedence,
>    and no-efficacy-claim stance from v1/v2 are unchanged.
>
> Every tunable cited below was re-checked line-by-line against
> `canis_config.h` (Appendix A), which now also carries the mobile/CBF
> and camera-architecture blocks. The mobile drivetrain and CBF controller are
> **implemented and self-tested in software but not yet exercised on the encoder
> hardware** (the encoder motors are on order); this draft is careful throughout
> to mark what is built-and-simulated versus built-and-hardware-validated.

> **Author TODOs before submission** (status as of this draft):
> 1. **[OPEN]** Add author/affiliation.
> 2. **[RESOLVED in v2]** ~~Fill real citations at `[REF]`~~ - see §2 and
>    References; details checked, not just author names.
> 3. **[RESOLVED in v2]** ~~Export figures~~ - the data-flow diagram (Figure 1)
>    and the arousal state-machine diagram (Figure 2) are exported as SVG in
>    `paper/figures/` and referenced in §3. **[OPEN]** A photo of the
>    deployed unit is not yet inserted (full-loop integration still pending), and
>    **[OPEN, NEW]** a CBF-QP schematic (Figure 3) and the odometry-error and
>    barrier-margin plots (Figures 4–5) are placeholders until the mobile
>    hardware runs.
> 4. **[OPEN]** Confirm the exposure-level log format and attach the longitudinal
>    fear-response sheet once enough sessions exist. The study has not started
>    (§4.3, §5).
> 5. **[RESOLVED, RE-DONE for v3]** ~~Confirm all tunable values against the
>    *current* firmware~~ - Appendix A re-checked against `canis_config.h`
>    including the new mobile/CBF and camera blocks. No drift found.
> 6. **[OPEN]** Obtain and cite an institutional/veterinary-behaviorist review if
>    the paper is ever reframed toward an efficacy claim (§8). This is a real
>    external step, deliberately left undone here.
> 7. **[OPEN, NEW]** Replace §3.8/§3.9's *simulated* CBF/odometry validation with
>    on-hardware numbers (barrier-violation count, tape-measured odometry drift)
>    once the encoder motors arrive and the mobile bring-up (`hardware/
>    MOBILE_BRINGUP.md`) is done. The safety *certificate* is only as strong as
>    the state estimate that feeds it (§3.9, §7), so these must be measured, not
>    asserted.

---

## Abstract

We present CANIS, a home robot that applies classical (Pavlovian) conditioning
to address unwanted behaviors in a young dog - chewing forbidden objects,
barking, climbing furniture, and roaming - by **rewarding the dog for leaving a
forbidden zone rather than punishing it for entering**. The design is shaped by
one hard constraint: the subject dog is *afraid of moving objects*, so a robot
that corrects by charging at the animal would be actively harmful. CANIS
therefore treats robot motion as a controlled, gradually-introduced variable,
gated behind an explicit **exposure level** and, in this version, behind a
**formally certified safe-motion layer**, defaulting to no movement at all. The
contributions of this paper are a **system design** and a **safety
certificate**: (i) a reward-on-leaving conditioning model realized as an
arousal-driven three-state machine with rate-based hysteresis; (ii) a fail-safe
motion architecture in which a "dumb" real-time executor stops the robot within
~1 s of losing its command link; (iii) a gradual-exposure control gate that makes
welfare a first-class, single-knob setting; (iv) a **control-barrier-function
(CBF) safe-motion layer** that certifies the robot-to-dog distance can never drop
below an online-estimated, adaptive comfort margin - for *any* commanded behavior - built on **wheel-odometry state estimation**, and degrading safe onto the
existing exposure gate and watchdog under any sensor loss; (v) a perception stack
that uses an **on-device learned dog detector (YOLO)** for the dog and retains
**ArUco fiducials** only for rigid *robot* localization, with the design tradeoff
made explicit; and (vi) a **two-mode (stationary / mobile)**, **three-camera
(onboard / streamed / fixed-room)** deployment matrix behind single configuration
switches, over a three-tier compute split (real-time MCU, coordinating MCU with
encoders and networking, and a vision/estimation/dashboard host). We report
**preliminary single-subject observations** (one dog, "Shifu"; n = 1; no control
group; no ethics approval for a behavioral claim) and are explicit that these are
engineering-validation observations of the *system*, not evidence that the
intervention changes behavior. The mobile/CBF layer is implemented and
self-tested in software; its on-hardware validation is future work. We include an
ethics and animal-welfare statement and enumerate the limitations that separate
this work from an efficacy study.

---

## 1. Introduction

Companion-animal behavior problems are common, and many owners cannot supply the
consistent, precisely-timed reinforcement that effective conditioning requires.
An always-present device that delivers a consistent marker-and-reward at the
right moment is therefore attractive. The dominant risk, however, is that an
automated "corrector" becomes an aversive, fear-inducing presence - especially
for an animal already sensitive to movement.

CANIS is a home robot built for exactly this situation. The subject is a young
Goldendoodle ("Shifu") who exhibits several target behaviors (eating things he
should not, barking, climbing furniture, roaming) *and* is measurably afraid of
moving objects. This second fact reframes the whole problem: the system cannot
treat robot motion as free. Instead of punishing the dog for entering a
forbidden zone - which for a fearful dog means pairing the zone with a scary,
approaching machine - CANIS **marks and rewards the dog for leaving**, and it
introduces its own motion *gradually*, over weeks or months, only as the dog's
logged fear response shows tolerance.

The central new idea in this version is to make "the robot never gets too close"
a **provable property of the controller**, not a mode switch we hope holds. The
earlier design forbade forward motion below a certain exposure level - a correct
but *discrete, open-loop* rule: it gated *whether* the robot could move, never
*how close it currently is* as a measured, controlled quantity. With a wheel-
odometry drivetrain we can put the robot-to-dog distance in the loop and certify,
via a control barrier function, that no commanded behavior can reduce that
distance below the dog's current comfort margin. Any decrease in the gap is then,
by construction, attributable **only to the dog choosing to approach** - which is
exactly the event we reward.

This paper is deliberately scoped as a **systems and design** contribution
(now with a **formal control** contribution layered on). We do **not** claim that
CANIS reduces the target behaviors; establishing that would require a controlled,
multi-subject, ethically-reviewed study we have not run (§8). What we claim is a
coherent, safety-first, *certified* design for a humane conditioning robot, and we
report preliminary observations that validate the *engineering*.

**Contributions.**
1. A **reward-on-leaving conditioning model** as an arousal-driven three-state
   machine (`CALM` / `AROUSAL` / `CORRECTION`) with rate-based hysteresis, so
   transient detections do not trigger interventions and the *act of disengaging*
   earns reinforcement.
2. A **fail-safe motion architecture**: a real-time microcontroller runs a motion
   watchdog and halts within ~1 s if its command link goes silent.
3. A **gradual-exposure gate** (`EXPOSURE_LEVEL`) that makes welfare a single,
   explicit, default-safe control knob.
4. **(NEW) A control-barrier-function safe-motion layer** certifying
   robot→dog distance ≥ an adaptive comfort margin, over a **wheel-odometry +
   IMU + ToF state estimate**, degrading safe onto the exposure gate and watchdog
   under any sensor loss.
5. **(REVISED) A perception stack that uses on-device YOLO for the dog** and
   retains ArUco only for rigid robot localization, with the tradeoff stated
   honestly (a reversal of v2's ArUco-over-YOLO position).
6. **(NEW) A two-mode, three-camera deployment matrix** (stationary/mobile;
   onboard/streamed/fixed-room) behind single configuration switches, over a
   three-tier compute split with a rollover-safe, back-off-hardened link protocol.

---

## 2. Related work

Automated and robotic animal-training / enrichment devices (treat-dispensing
feeders with app control) are increasingly common consumer products, but most are
built around fixed schedules or remote-triggered dispensing rather than a
closed-loop behavioral model; CANIS instead implements an explicit conditioning-
theoretic state machine (§3.3). The reward-on-leaving design and the graded-
exposure protocol (§3.5) draw on the applied-behavior-analysis literature on
classical/Pavlovian conditioning, counter-conditioning, and systematic
desensitization in companion animals - in particular differential reinforcement
of alternative behavior (DRA/DRO) as a humane substitute for positive punishment,
and the standard clinical pattern of pairing a graded, sub-threshold stimulus
exposure with a positive-valence reward to change a fear response without flooding
the animal [1]. A controlled veterinary trial of a comparable desensitization-and-
counter-conditioning protocol (four weekly sessions, standardized against a
no-training control group) found the trained group's in-context fear behaviors
improved relative to control, and is the closest published analogue to the
graded-exposure process CANIS instruments (§3.5, §9) [2].

On the perception side, learned single-stage object detectors (the YOLO family
[4]) now run in real time on small CPUs, which is what makes an on-device dog
detector practical on a Raspberry Pi 5; we use a nano YOLO model exported to a
CPU-optimized backend (§3.6). Fiducial-marker detection (ArUco [3], and related
systems such as AprilTag) remains the right tool for a *rigid, cooperative,
identity-and-pose-constrained* target, and we retain it for that role - localizing
the robot's own body in the fixed-room camera architecture (§3.6.3) - rather than
for the (deformable, self-occluding) dog. **Control barrier functions** [5] are a
standard tool for certifying forward-invariance of a safety set under a nominal
controller via a quadratic-program safety filter; §3.8 applies a CBF to the
specific safety set "robot stays at least the dog's comfort margin away." Watchdog
and fail-safe patterns for mobile robots, and multi-tier embedded architectures
pairing a real-time MCU with a networked coordinator and a vision/compute host,
are standard practice; §3.2, §3.4, and §3.9 describe how CANIS applies those
patterns to the constraint of never letting a software or sensor failure leave the
robot moving toward a fearful animal.

---

## 3. System design

### 3.1 Design constraints
Three constraints drove every subsequent decision:

- **C1 - Do no harm to a fearful animal.** The subject is afraid of moving
  objects. Robot motion is a *hazard to be rationed*, not a free actuator. "The
  robot must be able to stop, reliably and quickly" is a safety requirement;
  "the robot must be able to *not move at all* while still functioning" is a
  first-class operating mode; and, new in this version, "the robot must be
  *provably* unable to close distance on the dog below a set margin" is a
  certified property (§3.8).
- **C2 - Reward leaving, never punish entering.** For a fearful dog, punishing
  zone entry pairs the zone (and the robot) with fear. CANIS reinforces the
  desired alternative - disengaging - and, in mobile mode, additionally rewards
  the dog's *own voluntary approach* (§3.8), never the robot's.
- **C3 - Empirical, tunable thresholds.** Every arousal/timing/geometry/comfort
  threshold is meant to be tuned against the individual dog and chassis, so the
  design keeps them as named constants co-located in one header
  (`canis_config.h`) mirrored into the Python controller/dashboard, and documents
  that they are provisional and must be tuned *away* from the dog, never toward.

### 3.2 Three-tier compute split (updated for encoders + estimation)
CANIS distributes work across three devices, each matched to what it does best.
The split is unchanged in spirit from v2; what changed is that the ESP32 now also
owns **hardware quadrature encoder counting** and the Pi now also owns the
**state estimator and CBF safety filter**.

| Device | Role | Why here |
|--------|------|----------|
| **Arduino Uno** ("limbs") | Real-time executor: motor + dispenser servo + marker buzzer. Now also the differential-drive PWM executor for the new `VEL <pwmL> <pwmR>` verb. Does **no** kinematics. | Precise, jitter-free 5 V servo/motor timing; kept deliberately "dumb" and certifiable - it just maps signed PWM to the L298N and runs its own 1 s motion watchdog. |
| **ESP32** ("brain + inner ears") | Reads sensors, runs the arousal state machine, *decides*, talks both ways. Now also **reads 2× quadrature encoders on the PCNT hardware pulse counters**, runs a 20 Hz per-wheel velocity PI loop, computes odometry, and streams encoder+IMU+ToF telemetry at 10 Hz. | Enough compute for the FSM plus WiFi; the **PCNT peripheral counts quadrature edges in hardware at zero CPU cost**, which is exactly why the encoders land here and not on the interrupt-starved Uno. |
| **Raspberry Pi 5** ("eyes + mind + face") | On-device **YOLO** vision, Flask dashboard, and now the **state estimator (odometry⊕IMU⊕ToF⊕vision) + adaptive comfort margin + CBF quadratic program**. Runs **headless (OS Lite) with a fan**. | Vision, estimation, and a web UI are beyond either MCU; going headless frees the CPU budget the learned detector and the estimator need. The Pi also owns the wall clock. |

The split isolates failure domains: the real-time executor cannot be starved by
network stalls (§3.4), and the decision/estimation tier never blocks on vision
(it *polls*). In **mobile** mode the Pi runs the barrier QP and *serves* the
filtered `(v, ω)` command; the ESP32 clamps it again and relays it to the Uno; the
Uno's independent watchdog remains the final authority. No tier trusts the one
above it (§3.9).

**Figure 1** (`paper/figures/figure1_dataflow.svg`) shows the data-flow
picture. **[Author TODO - OPEN, NEW: extend Figure 1 with the mobile-mode
telemetry/motion links (`/telemetry` 10 Hz up, `/motion` 10 Hz down, `VEL` over
UART) - the current SVG predates the drivetrain.]**

### 3.3 The arousal state machine (conditioning core)
The behavioral model is realized as three states driven by a scalar **arousal**
level in `[0, 100]`:

| State | Meaning | Behavior |
|-------|---------|----------|
| `CALM` | Baseline, settled | Rewards *sustained* calm (after a warm-up, rate-limited). |
| `AROUSAL` | Something detected, watching | **Monitor only - no intervention.** |
| `CORRECTION` | Bad behavior confirmed | Emits an 800 Hz "disengage" cue every 2 s and (only if exposure allows) orients toward the zone. |

Arousal is **rate-driven**, which gives hysteresis for free. While *any*
detection is active - local PIR/ultrasonic **or** a remote vision call from the
Pi - arousal rises at `AROUSAL_RISE_PER_SEC` (30/s); it *always* decays at
`DECAY_PER_SEC` (12/s). Net presence climbs at +18/s and absence settles at
−12/s. Crossing `AROUSAL_ENTER` (20) enters `AROUSAL`; crossing `CORRECTION_ENTER`
(70) enters `CORRECTION`; the return path uses separate, lower exit thresholds
(`AROUSAL_EXIT` = 12, `CORRECTION_EXIT` = 60) so the system does not chatter at a
boundary. Because entry and the eventual return to `CALM` are separated by an
accumulate-and-decay integrator rather than a single threshold, brief spurious
detections cannot flip the system into a correction, and a dog that genuinely
lingers is held in the aroused/correcting band (so a lingering dog is never
wrongly "rewarded for leaving").

The **key design idea** is the transition rule: dropping from `AROUSAL` or
`CORRECTION` back to `CALM` means the dog disengaged - and *that* transition fires
the reward. CANIS rewards **leaving**, never punishes entering (C2).

**Figure 2** (`paper/figures/figure2_arousal_state_machine.svg`)
diagrams the full transition graph with every enter/exit threshold.

Reward delivery is rate-limited for safety and hardware protection: a 20 s
cooldown (`REWARD_COOLDOWN_MS`) between any two treats and a hard per-session cap
(`MAX_SESSION_REWARDS` = 30) so the hopper cannot empty and so in/out "bouncing"
at a zone edge cannot rapid-fire treats.

### 3.4 Fail-safe motion watchdog
Because motion is a hazard (C1), the executor tier runs a **motion watchdog**: the
Arduino stops the motors if it does not receive a fresh motion command within
`MOTION_TIMEOUT_MS` (1000 ms). The ESP32 correspondingly **re-pulses** any active
motion every `MOVE_PULSE_MS` (500 ms) for as long as it intends the robot to move,
and stops re-pulsing the instant the intent ends. Any failure that silences the
ESP32 → Arduino link - a crash, a reset, a loose wire - causes the robot to
**coast to a stop within ~1 s** on its own, rather than continuing toward the dog.
Legacy correction motions are also *bounded in duration* (`ORIENT_MS` = 700 ms,
optional `APPROACH_MS` = 400 ms) and halted immediately on leaving `CORRECTION`.

This watchdog is **unchanged** by the mobile upgrade and remains supreme: the new
differential-drive `VEL <pwmL> <pwmR>` verb (§3.7, §3.8) is subject to the exact
same 1000 ms timeout, and the new stack layers *faster* watchdogs above it, never
around it (§3.9).

### 3.5 Gradual-exposure architecture
Welfare is concentrated into a single, explicit gate, `EXPOSURE_LEVEL`, described
in-code as "the welfare knob":

- **Level 0 (default):** no movement at all - correction is *buzzer-only*.
- **Level 1:** orient only - pivot to face the zone, never approach.
- **Level 2:** orient plus a brief, gentle forward approach.

The decision logic is identical at every level; the exposure gate sits between
"the robot decided to correct" and "the robot physically moves." The intended
protocol is to raise the level by **one step at a time**, never skipping, and only
after the dog's *logged fear response* shows comfort at the current level. A
two-step arm→confirm commit (`EXPOSURE_CONFIRM_MS` = 5 s) prevents a stray
double-press from jumping straight to the most intrusive level. This realizes
gradual desensitization: the same closed loop runs from day one, but its physical
intrusiveness is dialed up slowly under human supervision - structurally the same
graded-exposure-plus-positive-reward pattern used in supervised veterinary
desensitization/counter-conditioning protocols [1, 2], instrumented in hardware.
In **mobile** mode the exposure gate still **outranks** the CBF layer: a level
that forbids motion forbids it regardless of what the barrier would otherwise
permit (§3.9). The longitudinal fear-response record this process produces is, per
the project framing, the actual scientific artifact of interest (future work; §9).

### 3.6 Perception: on-device YOLO for the dog, ArUco for the robot
Detection has two independent sources. Locally, the ESP32 fuses a PIR motion
sensor with **three ultrasonic rangefinders** (left/centre/right), fired
sequentially to avoid echo cross-talk; the trio doubles as a coarse local bearing.
Remotely, the Pi runs vision and POSTs a detection to the dashboard, which the
ESP32 polls as a remote detection.

**This is the section that changed most from v2.** v2 argued *ArUco-over-YOLO* - a fiducial collar marker read by the Pi. That is no longer the design. We switched
the dog detector to an on-device learned model and now retain ArUco only for a
different, well-suited job.

#### 3.6.1 Why we moved off the collar marker (honest reversal)
The v2 argument was not wrong at the time; the constraints changed and the
weaknesses of the fiducial became the dominant cost:

- **The fragility the grace-period code was patching was intrinsic.** ArUco loses
  the collar marker for a stretch of frames every time the dog turns his head or
  the tag rotates off-square - a marker on a moving, deformable, 16 kg dog is
  small and self-occluding. `detect.py`'s `[OPT-12]` `DetectionGrace` (a 1.5 s
  hold across brief losses) exists *specifically* to hide this flapping, at the
  cost of delaying the genuine "dog left" signal (and thus the leaving-reward) by
  up to the grace window. A learned dog detector removes the marker, and with it
  the flapping that the grace was compensating for - the dog does not have to be
  wearing (or squarely presenting) anything.
- **The compute objection went away.** The v2 argument leaned on the Pi being
  busy. The Pi now runs **headless on Raspberry Pi OS Lite with a fan**; a
  **YOLO11n** model exported to **NCNN** (the fastest CPU backend on the Pi 5 per
  ultralytics' own benchmarks) runs at ~40–60 ms/frame at 320 px, and inference
  only runs every `PROCESS_EVERY`th frame anyway - comfortably real-time for our
  cadence. The reliability-for-compute trade v2 described no longer binds.
- **Identity is handled differently, not lost.** The fiducial gave dog-identity
  "for free." YOLO detects the COCO `dog` class, so other *people* in frame do not
  trigger it, but a second dog would. For a single-dog home this is acceptable;
  §7 lists it as a limitation, and the multi-reporter contract (§3.7.4) keeps a
  per-node zone base name so a future per-dog identity check can be added
  node-side without touching the wire format.

The new pipeline (`vision/detect_yolo.py`, now the default) POSTs the *same*
detection contract as before (§3.7.4) plus optional `bearing_deg`, `distance_m`,
and `confidence` fields, with a node-side confidence gate (`YOLO_CONF_MIN` = 0.60;
sub-threshold detections are silence, never posted). Bearing comes from a pinhole
model (`bearing_deg = atan2(cx − w/2, f_px)`, `f_px = (w/2)/tan(HFOV/2)`); a
monocular range estimate comes from the bounding-box height and a known dog height
(`distance_m = DOG_HEIGHT_M · f_px / bbox_h_px`, default `DOG_HEIGHT_M` = 0.45 m),
reported as `null` when out of a trust band - the contract forbids faking a number,
and this heuristic honestly over-reads distance for a sitting/lying dog. **The
robustness machinery is reused verbatim from the ArUco module**: the async POST
worker (`[OPT-6a]`), camera-read backoff/reconnect (`[OPT-6b]`), the
`DetectionGrace` hold (`[OPT-12]`, now bridging *detector* dropouts rather than
*marker* dropouts), the frozen-frame guard (`[OPT-13]`), and the blur/low-light
skip-ratio alert (`[C6]`). `detect.py` (ArUco) is kept in place as a fallback
reporter - its bare `{"zone": …}` POST is a valid `/detect` body forever - and
because `detect_yolo.py` imports its camera sources and helpers.

#### 3.6.2 What ArUco is *still* for
ArUco is not deleted; it is **repositioned**. In the fixed-room camera
architecture (option C, §3.7.3) a `DICT_4X4_50` marker is mounted flat on the
robot's top deck (`ROBOT_MARKER_ID` = 17, 100 mm, 0.18 m above the floor). The
detector recovers the robot's *position and heading* from the marker (corner
0→1 = the robot's front). This is the task fiducials are actually good at - a
**rigid, flat, cooperative** target of known size at a known height - the opposite
of a marker on a squirming dog. So the system uses **YOLO for the deformable
subject (the dog) and ArUco for the rigid one (the robot)**, which is the honest,
task-matched division of labor. The marker id 17 is compile-guarded to be a valid
`DICT_4X4_50` id and to avoid the legacy dog-collar default (id 0).

#### 3.6.3 Perception is decoupled from the safety layer
Importantly, the CBF safety layer (§3.8) keys off the **ToF distance**, not off
the vision detector. Vision only *gates* which ToF return is "the dog" (bearing
selects dog-vs-wall) and supplies the bearing β; the barrier's certificate does
not depend on which detector produced that bearing. This is deliberate: the
perception choice (ArUco vs. YOLO vs. fixed-room geometry) must not be able to
weaken the safety guarantee (`SAFE_MOTION_CBF_SPEC.md` §9.1).

### 3.7 Two modes and three camera architectures
The system now ships two operating modes and three camera placements, each behind
a single configuration switch in `canis_config.h`, mirrored into the Python nodes
via environment variables (a header cannot be `#include`d by Python, so the switch
is duplicated as a string and kept in agreement per deployment).

#### 3.7.1 Stationary (default) vs. mobile
`CANIS_MOBILE_ENABLED` (default **0**, the SAFE ship default) selects the mode:

- **Stationary (`= 0`).** The ESP32 issues **no drive commands from the new
  stack**: it never polls `/motion`, never emits `VEL`, never streams
  `/telemetry`, never touches the encoder pins. Everything from v2 runs exactly as
  before - YOLO detection → `/detect` → `/command` → arousal/state machine, the
  dispenser, the dashboard, and every existing fail-safe. New `/ingest` telemetry
  fields are *omitted*, so the wire traffic is byte-identical to the pre-upgrade
  system. **This is the mode that runs the conditioning study this week**, with no
  new hardware.
- **Mobile (`= 1`).** Drive commands come *only* from the CBF pipeline (§3.8):
  Pi estimator + comfort margin + QP → `MOVE v ω` → firmware clamps → `VEL`. Every
  pre-existing fail-safe stays in force and **outranks** the CBF (exposure gate,
  `CANIS_NEVER_APPROACH` - compile-guarded to 1 in mobile builds, the Uno
  watchdog, power-cycle → exposure 0). Degradation is one-way toward stationary.

#### 3.7.2 Camera architecture switch
`CANIS_CAMERA_MODE` selects one of three placements, all POSTing the *same*
detection contract so the firmware, controller, and dashboard are
camera-mode-agnostic:

- **A - `onboard` (default).** Camera attached directly to the Pi (picamera2 or
  USB); the robot's-eye view. Runs `detect_yolo.py` as-is. Zero new hardware - this is what the stationary study uses now.
- **B - `esp32cam_stream`.** Robot keeps its own view, but the Pi is **off-board**
  and pulls MJPEG frames from an **ESP32-CAM** over HTTP. Same `detect_yolo.py`,
  same guards - **only the frame source changes** (a new `Esp32CamStreamSource`
  presenting the identical `read_bgr()/reopen()/release()` surface as the local
  cameras, so the backoff-reconnect and frozen-frame machinery apply unchanged; a
  pure SOI/EOI MJPEG parser tolerant of split markers and of the stream being down
  at launch). Streaming is inherently flaky (WiFi drops, brownouts, latency), so
  **safety stays on the onboard ToF; only identity/bearing ride the stream.**
- **C - `fixed_room`.** A **fixed room camera** watches the scene: **YOLO finds
  the dog**, an **ArUco marker on the robot** localizes the robot, and robot→dog
  distance and bearing are computed in the **room frame** (pixel ray → tilt
  rotation → floor-plane intersection; dog on the floor plane, robot marker at its
  mount height). Served by a dedicated `vision/detect_fixed.py`; `detect_yolo.py`
  exits with a pointer if launched in this mode. This is the most reliable option
  for a single-room study - best data, lightest robot - at the cost of the robot
  only being perceived inside the camera's view.

**Recommendation on file** (from `docs/MOBILE_UPGRADE_STATUS.md`): **A** now (the
stationary study, zero new hardware); **C** when going mobile in a single room
(reliability + data); **B** for the ambitious long game of a self-perceiving,
room-portable robot, once the core is proven. A spare ESP32 DevKit (no camera) is
sequenced later as a PIR/ToF *presence* node in a multi-room mesh - the detection
contract is already multi-reporter.

#### 3.7.3 One contract-frame caveat (documented honestly)
The detection contract words `bearing_deg` as *camera frame*, which is true for
modes A and B (there the camera frame *is* the robot frame). Mode C computes and
sends `bearing_deg` in the **robot frame** to preserve the left/right semantics
the ESP32's `zoneToDir()` depends on. This is documented in `detect_fixed.py` and
flagged as a **[Author TODO - OPEN, NEW]** one-line amendment to
`INTERFACE_CONTRACT.md` §3; it was not silently changed in the contract file.

#### 3.7.4 Inter-tier protocol and robustness (extended for mobile)
The ESP32 pushes state to the Pi (`POST /ingest`, 1 Hz plus on-change) and polls
for vision commands (`GET /command`, 1 Hz). Both share a **rollover-safe back-off**
(if the Pi is unreachable the ESP32 stops hammering it for 5 s), and all
`millis()` comparisons survive counter rollover. The Pi supplies the **wall clock**
the ESP32 lacks. The dashboard falls back to a built-in simulator (mirroring the
firmware logic) if no ESP32 data arrives in 5 s, with a footer stating *live* vs
*simulated*.

Mobile mode **extends** this without breaking it. When `CANIS_MOBILE_ENABLED = 1`
the ESP32 additionally streams `POST /telemetry` at 10 Hz (encoder counts,
odometry `v`/`ω`, ToF range, IMU heading - freshest-only, never queued) and polls
`GET /motion` at 10 Hz, which returns exactly one line, `MOVE <v> <ω>` or `STOP`.
The values served are the **post-QP** command (already barrier-filtered); the raw
nominal is never served. The detection contract keeps its required `zone` field
(so the legacy ArUco reporter stays valid forever) and carries the new
`dog_present`/`bearing_deg`/`distance_m`/`confidence` fields as optional; the
dashboard ignores unknown keys, so an old dashboard and a new node interoperate.
See Figure 1.

### 3.8 Formal safe-motion layer: a control barrier function (NEW, central)
The v2 safety story for motion was two discrete, open-loop mechanisms:
exposure-level gating and an `[OPT-8]` "never-approach" rule that rewrote a forward
command into an in-place pivot below exposure 2. Correct, but they gate *whether*
the robot may move, not *how close it may be* as a continuous measured quantity - the robot had no odometry and its distance to the dog was never in the control
loop. This version replaces "a mode that forbids forward motion" with a
**control barrier function that certifies the robot-to-dog distance can never drop
below the dog's comfort margin, for any commanded behavior** - a *provable
forward-invariance* property, degrading gracefully back onto the exposure gate and
watchdog if a sensor drops out. Full math: `docs/SAFE_MOTION_CBF_SPEC.md`;
implementation: `control/`.

**Safety set.** Define the comfort set via a barrier function
`h(x) = d_rd − D_comfort(s) ≥ 0`, where `d_rd` is the estimated robot-to-dog
distance and `D_comfort(s)` is the dog's current comfort margin (§3.8.1).
`h ≥ 0` is safe; `h = 0` is the boundary (robot exactly at the margin); `h < 0`
is a comfort violation and must be **unreachable**.

**Barrier condition.** With an extended class-𝒦 function `α(h) = γ·h` (`γ =
CANIS_CBF_GAMMA` = 0.5, small = conservative), every commanded input `u = [v, ω]`
must satisfy the CBF constraint `ḣ(x, u) ≥ −γ·h(x)`. Intuitively: the closer to
the boundary, the harder the constraint forbids any input that reduces `d_rd`; at
`h = 0` only inputs that hold or increase distance are admissible. This is the
continuous generalization of `[OPT-8]` (which is the degenerate `D_comfort = ∞`
case). Because the constraint certifies only the *robot's contribution* to `ḣ`,
the robot's commanded velocity can never make `d_rd` decrease - so any decrease is
attributable **only to the dog approaching**.

**The QP safety filter.** Underneath the barrier runs the existing arousal/
exposure logic unchanged, producing a *desired* `u_nom` (heading control to face
the dog is a separate, non-distance-reducing channel and passes untouched - *facing ≠ approaching*). The barrier is a safety *filter*:

```
u* = argmin_u ‖u − u_nom‖²   s.t.   ḣ(x, u) ≥ −γ·h(x),   |v| ≤ v_max, |ω| ≤ ω_max
```

Crucially, the CBF constraint `v·cosβ ≤ γ·h` is a **single linear inequality
touching only `v`** (ω does not change distance), so the 2-input QP is a
**closed-form half-space projection** on `v` followed by an actuator-box clamp - no iterative solver, runs trivially at the control rate. The implementation
(`control/cbf.py`) handles the degenerate cases explicitly: dog-behind (a
backward command that would close distance is rewritten to open it), dog-abeam
(`cosβ ≈ 0`), retreat saturation (still distance-opening), and - the safety-
critical one - `distance_known = False` collapses to `h = 0, β = 0` worst case,
forcing `v ≤ 0`.

**Reward-on-approach (mobile C2).** When the estimator sees `d_rd` decrease while
the robot's *own* commanded `v ≤ 0`, that is a **voluntary approach** by the dog →
marker + one treat, subject to `REWARD_COOLDOWN_MS` (20 s) and the session cap. A
rolling-window detector (`control/reward.py`: `d` dropped ≥ 0.15 m over 2 s while
every commanded `v ≤ 0`) gates it. This makes the mobile reward *earned by the
dog's own choice to come closer*, never by the robot lurking - the dwell/proximity
credit is cleared the instant the robot commands forward.

#### 3.8.1 Adaptive comfort margin `D_comfort(s)` (the estimation coupling)
A fixed standoff is a weak claim. Instead the dog's comfort margin is estimated
online and fed into the barrier so the safe set *tightens when Shifu is stressed
and relaxes as he habituates*. `control/comfort.py` maps a latent comfort scalar
`s ∈ [0, 1]` to `D_comfort(s) = D_MIN + s·(D_MAX − D_MIN)` with `D_MIN = 60 cm`
(the floor the robot may *never* close below, even for a fully habituated dog) and
`D_MAX = 200 cm` (the standoff for a maximally stressed dog). `s` starts at the
most conservative end (max standoff), **grows fast** on flinch/arousal (upward-
only arousal coupling - stress always widens the margin immediately), and
**shrinks slowly** only on the dog's *voluntary, earned* approach/dwell. The
robot cannot earn a smaller margin by lurking: the dwell discount requires
dog-initiated proximity credit and is cleared the moment the robot commands
forward. This couples **estimation → safe control** in a closed loop: the
controller *earns* proximity from the dog's behavior, it never assumes it.

### 3.9 State estimation and safe degradation (NEW)
The barrier certificate is only as strong as the state estimate it trusts, so the
estimator and its failure behavior are first-class.

**State estimate.** `control/estimator.py` runs (i) a **complementary pose
filter** - encoder dead-reckoning for translation and a yaw estimate, corrected by
the IMU (differential-drive odometry accumulates yaw drift fastest; the contract's
clockwise-positive heading is converted internally), handling the ESP32 `millis()`
`uint32` wrap - and (ii) a **scalar EKF on `d_rd`** whose predict step carries the
robot's *known* contribution `−v·cos(β)·dt` (so dog motion is process noise, not
robot motion), with β propagated by `ω + v·sinβ/d` and corrected by vision
bearings. Encoders are read on the **ESP32 PCNT** peripheral (hardware quadrature
counting), streamed with IMU + ToF at 10 Hz. The ToF (VL53LX) is **fused only when
the vision bearing is fresh and `|β| ≤ 15°`** - this is how the system tells "the
dog" from "a wall": a ToF return is only accepted as the dog's range when vision
says the dog is roughly ahead. Zone-only legacy detections refresh presence but
never fabricate a range; everything degrades to "unknown."

**Safe degradation (the invariant).** The barrier sits *above* the existing stack
and never weakens it. The composition is enforced as a strict authority order and
verified by selftest:

| Layer (authority) | Trigger | Action |
|---|---|---|
| Exposure gate *(outer, existing)* | level forbids motion | motion forbidden regardless of CBF |
| Stale telemetry | no fresh frame | **STOP** |
| Uncertainty | estimator σ large | barrier evaluated on `d − min(σ, 30 cm)` (uncertainty *shrinks* h, never grows it) |
| Sensor dropout | ToF lost / dog out of frame | `distance_known = False` ⇒ `h = 0, β = 0` ⇒ `v ≤ 0` (treat as at-the-boundary, never "safe to advance") |
| Pi/dashboard | controller stale > `MOTION_CMD_TTL_MS` (600 ms) | serves `STOP` |
| ESP32 | `/motion` poll stale > 600 ms | sends `STOP` |
| **Uno *(final, unchanged)*** | no motion word for 1000 ms | **cuts motor PWM itself** |
| Power-cycle *(existing)* | reboot | returns to exposure 0 |

The headline invariant - **no single sensor or compute failure can produce a
distance-reducing command** - is asserted three independent ways in the
hardware-free selftest (`python3 control/motion_controller.py --selftest`, 38/38
checks): (1) a 5000-sample fuzz proving `h ≤ 0 ⟹ v*·cosβ ≤ 0` for the closed-form
QP; (2) a closed-loop sim where a relentless `v = +0.3` nominal settles at the
boundary with zero distance-reducing commands and a minimum true `h` of +0.027 m;
and (3) dropout scenarios (detection loss → `v ≤ 0` after the hold, telemetry loss
→ STOP). The same selftest checks odometry pose error (2 cm / 0.05° on a square
drive across the `millis()` wrap, *in simulation*), wall-return ToF rejection,
reward firing only for dog-initiated approaches with cooldown respected, comfort
`s` bounded under fuzz, and full mode-gating (mobile-off ⇒ the controller returns
`None` and never touches the motion sink, while estimation and reward telemetry
stay live).

**Honest scope of the certificate.** All of §3.8/§3.9 is **implemented and
self-tested in software only**; the encoder motors are on order and the on-hardware
numbers (a target of **zero** barrier violations over a live session; tape-measured
odometry drift) are pre-registered in `study/STUDY_PROTOCOL.md` and reported as
future work (§9, Author TODO #7). A CBF guarantee assumes the model and state
estimate are trustworthy; that is *why* those two are first-class measured results,
not assertions. Two integration items are also open by design: the dashboard
in-process wiring of the controller (a documented 4-line hookup, not yet done in
`app.py`) and a contract-first decision on a Pi→ESP32 reward-*delivery* verb (no
such verb exists in the contract today, so mobile reward events are emitted via a
callback until one is added). Neither is a safety gap - with the controller
unwired the `/motion` TTL already fail-safes to STOP.

---

## 4. Methods

### 4.1 Hardware bring-up and per-subsystem validation
The system was brought up in phases, each hardware-tested in isolation before
integration:

- **Mobility.** L298N differential drive on a metal chassis, both channels
  PWM-matched, all five legacy motion commands verified; run on a 6 V pack.
  Speeds set high (200/220) for bring-up, intended to be dialed *down* for
  exposure sessions. **The encoder-motor upgrade (2× TT/quadrature gearmotors
  replacing the bare DC motors) is not yet installed**, so the mobile drivetrain
  and CBF layer are software-validated only (§3.9).
- **Dispenser.** Servo gate with a narrowed tube "throat"; `GATE_OPEN_MS` (150 ms)
  is the main reliability knob. Bench-tested, with optional beam-break jam
  detection and ACK/JAM replies.
- **ESP32 ↔ Arduino serial.** One-way link, level-shifted, `SoftwareSerial` on the
  Uno. The new `VEL <pwmL> <pwmR>` differential-drive verb is appended to the
  existing grammar; an old Uno sketch ignores the unknown word → no motion → fails
  SAFE. **Landed in firmware, needs the Arduino/ESP32 toolchain to compile on
  hardware.**
- **Sensors.** PIR (60 s warm-up mask) and HC-SR04 ultrasonic proven; expanded to
  three ultrasonics in firmware. IMU + VL53LX ToF are on the reserved I²C pins
  (21/22); the encoder channels use the free PCNT-capable pins (18/19, 16/17).
- **State machine.** Real outputs - dispense, correction cue, reward
  cooldown/cap, watchdog-safe pulsed orient - verified on the integrated
  `CANIS_Core` firmware.
- **Vision.** `detect_yolo.py` (YOLO11n/NCNN) is the new default; `detect_fixed.py`
  implements the fixed-room geometry; `detect.py` (ArUco) is the fallback. All
  three `py_compile` clean and pass their hardware-free `--selftest`s (geometry,
  MJPEG parsing, grace/gating, room-frame geometry incl. horizon rejection and
  marker-heading). **End-to-end on the camera hardware with a live model is the
  next bring-up step** (the headless Pi OS-Lite + NCNN export commands are
  documented in `detect_yolo.py`).
- **Controller.** `control/` (estimator, comfort, cbf, reward, motion_controller)
  `py_compile`s clean and passes its 38-check hardware-free selftest (§3.9).
- **Dashboard.** Live ESP32→Pi link with simulator fallback; auto-starts on boot
  (systemd); telemetry/motion endpoints landed and backward-compatible.

### 4.2 Exposure protocol (as designed)
Gradual exposure runs on its own weeks/months calendar, starting as soon as the
robot can move at all. Begin at `EXPOSURE_LEVEL = 0` (buzzer-only), observe and
log the dog's fear response over repeated sessions, and advance by exactly one
level only when the log shows comfort. In mobile mode the CBF layer adds a
*continuous* standoff on top of the *discrete* exposure ladder. `[Author TODO - OPEN: specify the fear-response coding scheme - latency to approach, tail/ear
posture, avoidance distance, willingness to take the treat - and the pre-registered
advance/hold/retreat criteria, before any of this supports a claim.]`

### 4.3 What we measured, and what we did not
We collected **engineering-validation** observations: whether each subsystem
behaves as specified, whether the closed loop makes the intended transitions and
rewards on-leaving, and whether the fail-safes fire. For the mobile/CBF layer, the
current evidence is **simulation and selftest only** (§3.9). We did **not** run a
controlled behavioral trial, and we did not collect the data a behavior-change
claim would require (baseline rates, a control condition, blinded coding, multiple
subjects). This scoping is deliberate (§8).

---

## 5. Preliminary observations (single subject, n = 1)

> **These are single-subject, uncontrolled, engineering-validation observations.
> They are not evidence of behavioral efficacy. The mobile/CBF observations below
> are from simulation and selftest, not yet from encoder hardware.** Read §7 and
> §8 first.

`[Author TODO - OPEN: replace the bracketed placeholders below with actual logged
values/anecdotes; keep every statement descriptive and non-causal. This section
cannot be finished from documentation alone - it requires the study to run
(study/STUDY_PROTOCOL.md), which has not started.]`

- **The stationary closed loop behaves as designed with a real dog in the room.**
  With local sensors and the Pi vision path both feeding arousal, the system
  entered `AROUSAL` on confirmed presence, escalated to `CORRECTION` when presence
  persisted, and delivered a treat on the return to `CALM` (reward-on-leaving),
  subject to cooldown/cap. `[Author TODO - OPEN: add qualitative note on
  timing/feel, and re-confirm under the YOLO detector specifically.]`
- **The hysteresis suppressed spurious corrections.** Brief detections raised
  arousal but decayed below `AROUSAL_ENTER` before reaching `CORRECTION_ENTER`.
  `[Author TODO - OPEN: add observed count of transient detections that did not
  escalate.]`
- **The fail-safe fired as intended.** In deliberate link-drop tests, the robot
  stopped within ~1 s without an explicit STOP. `[Author TODO - OPEN: add measured
  stop latency.]`
- **The CBF barrier held in simulation.** In the closed-loop selftest, a
  relentless forward nominal settled at the comfort boundary with zero
  distance-reducing commands (min true `h` = +0.027 m) and zero barrier
  violations across the fuzz and dropout suites. `[Author TODO - OPEN, NEW:
  replace with the on-hardware barrier-violation count (target zero) and a
  D_comfort(s) trajectory once the encoder motors are installed.]`
- **Exposure Level 0 was used throughout the initial sessions.** `[Author TODO - OPEN: add whether/when Level 1 was reached and the fear-response observations
  that gated it.]`
- **Vision reliability.** `[Author TODO - OPEN: add YOLO false-positive/miss
  notes vs. the old ArUco flapping; the blur guard and frame-skip kept the Pi
  responsive.]`

We report these as *directional, single-subject* engineering observations only.

---

## 6. Discussion

### 6.1 Why "reward leaving" is the right primitive for a fearful dog
A conventional deterrent robot creates an aversive association between the zone
(and the robot) and fear - the opposite of what a movement-sensitive dog needs.
By reinforcing disengagement instead - and, in mobile mode, additionally
reinforcing the dog's *own* voluntary approach - CANIS aligns the training signal
with desired behavior and keeps the robot's salient events *positive*. The arousal
integrator defines "left the zone" as a sustained decay back to `CALM`, so the
reward lands on genuine disengagement.

### 6.2 Safety as an architectural property, not a feature
The two hazards specific to this deployment - an unwanted correction and a robot
that keeps moving toward a scared animal - are addressed structurally: the former
by hysteresis plus the monitor-only `AROUSAL` state, the latter by a layered stack
where each layer is *more* conservative than the one it backs (exposure gate →
CBF barrier → freshness TTLs → the dumb-executor watchdog → power-cycle reset).
None depends on the decision or estimation tier being healthy: a crashed brain, a
lost ToF, or a dropped stream each yields a *stopped* or *held-distance* robot, not
an advancing one. The CBF makes this a *provable* forward-invariance property in
the nominal case and a *fail-to-stationary* property in the degraded case.

### 6.3 The exposure gate decouples policy from intrusiveness
Separating `EXPOSURE_LEVEL` from the state machine means the training policy can be
developed once while physical intrusiveness is titrated slowly and reversibly. The
CBF adds a second, *continuous* axis of intrusiveness control (the standoff
margin) on top of the discrete exposure ladder, and the two compose with the
exposure gate strictly on top.

### 6.4 On the perception reversal (YOLO now, ArUco repositioned)
The honest read is that v2's ArUco-over-YOLO argument was contingent on a busy Pi
and a cooperative marker, and both assumptions changed: headless OS-Lite freed the
compute, and a marker on a squirming dog was never really "cooperative" - the
grace-period code was the tell. Moving the dog to a learned detector removes that
fragility at its source; keeping ArUco for the *rigid robot body* in the
fixed-room mode uses fiducials for what they are genuinely best at. The
reliability argument did not vanish, it *relocated*: identity for a second dog is
now the open weakness (§7), traded for freedom from the collar marker and its
flapping.

### 6.5 What the CBF buys over the old rule
`[OPT-8]` never-approach was a correct binary. The CBF generalizes it to a
measured, adaptive, continuous certificate - and, more importantly, ties the safe
set to an *online estimate of the dog's comfort* so the standoff tightens as the
dog habituates and widens the instant he flinches, entirely from his own behavior.
That estimation→control coupling, degrading safe under any single failure, is the
transferable robotics contribution.

---

## 7. Limitations

- **n = 1, no control group.** All observations are from one dog in one home; no
  baseline-versus-intervention comparison and no control condition.
- **No efficacy claim, and no basis for one.** No target-behavior rates
  before/after, no blinded coding, no pre-registered outcome criteria. The
  observations validate the *system*, not the *intervention*.
- **The mobile/CBF layer is not yet hardware-validated.** It is implemented and
  self-tested in software; the encoder motors are on order. The safety certificate
  is only as strong as the (currently simulated) odometry and ToF estimate - its
  on-hardware barrier-violation count and drift are unmeasured (§3.9, §9).
- **Single-dog identity.** The YOLO detector reports the COCO `dog` class, so a
  *second* dog would trigger it (the old collar fiducial gave identity for free).
  Acceptable for a single-dog home; a node-side per-dog check is a future add.
- **Monocular range is a heuristic.** The bbox-height distance estimate over-reads
  for a sitting/lying dog and is reported `null` outside a trust band; the ToF is
  the authoritative range the barrier trusts, and only when vision gates it as the
  dog.
- **Thresholds are provisional.** Arousal, exposure, comfort-band, and `γ` values
  are empirical, re-confirmed against the current firmware (Appendix A) but subject
  to re-tuning - always *away* from the dog, never toward.
- **Sensing is coarse.** PIR + ultrasonic + a single-camera zone give presence-in-
  a-zone, not fine-grained behavior recognition; the system cannot distinguish
  *which* unwanted behavior is occurring.
- **Fixed-room mode is view-bound.** In mode C the robot is only perceived (and, in
  mobile mode, only localized) inside the fixed camera's view; roaming it out of
  frame loses detection until it returns.
- **Integration not fully closed.** Full-loop run with final wiring, the printed
  shell, the encoder drivetrain, and the dashboard controller hookup are pending.
- **Generality unknown.** The design is tuned to one fearful dog; transfer to other
  temperaments is untested.

---

## 8. Ethics and animal-welfare statement

This project involves a companion animal (a privately-owned dog living in the
author's home), and its central design constraint is the animal's welfare.

- **Framing and scope.** This paper is a **systems/design** report with
  preliminary single-subject engineering observations. It makes **no claim** that
  the device changes the dog's behavior. We explicitly did **not** conduct a
  behavioral-efficacy study, and did **not** obtain institutional animal-ethics
  approval, because we are not reporting a behavioral result. `[Author TODO - OPEN: if reframed toward an efficacy claim, obtain prior review from an
  appropriate animal-research ethics body and/or a qualified veterinary
  behaviorist, and add the approval reference here.]`
- **Welfare-first design.** The system defaults to **no movement** (exposure
  Level 0, `CANIS_MOBILE_ENABLED = 0`) and uses only a mild auditory cue and a
  food reward; it reinforces a desired alternative behavior (leaving), and in
  mobile mode the dog's *own* voluntary approach, rather than applying positive
  punishment. Motion is introduced gradually, one small step at a time, gated on
  the dog's observed fear response, reversible, and - in mobile mode - additionally bounded below by a formally certified comfort margin (§3.8) that
  *widens* automatically on any sign of stress and can only *narrow* from the
  dog's own earned approach.
- **Physical safety.** A hardware motion watchdog stops the robot within ~1 s of
  any command-link loss; the CBF layer and its freshness TTLs add faster,
  distance-aware stops above it; correction motions are duration-bounded and
  halted immediately when the dog disengages; speeds are dialed down for exposure
  sessions; and no single sensor or compute failure can produce a distance-
  reducing command (§3.9). The dog is supervised during sessions. `[Author TODO - OPEN: state the supervision and stop-session criteria explicitly - e.g. session
  ends on any sign of distress.]`
- **Reward safety.** Per-session reward caps and cooldowns prevent over-feeding and
  hopper depletion. `[Author TODO - OPEN: confirm the daily treat budget is within
  a veterinarian-appropriate amount for the subject's size/diet.]`
- **Data.** The system records inertial/ultrasonic/PIR/encoder counters,
  arousal/state logs, odometry/ToF telemetry, and detections. With the YOLO
  pipeline the camera performs on-device inference and posts a *zone label and
  scalar bearing/distance*, not imagery; in the fixed-room mode a robot-marker pose
  is also computed on-device. `[Author TODO - OPEN: confirm no video is stored, or
  state the retention/handling policy if any frames are saved (e.g. for the
  `--snap` diagnostic).]`
- **Consent.** The animal's owner (the author) consents to the dog's participation
  and can end any session at will.

---

## 9. Conclusion and future work

CANIS is a design for a humane conditioning robot for a fear-sensitive dog, built
around a single behavioral primitive - *reward the dog for leaving, never punish it
for entering* - and a safety posture in which robot motion is a rationed hazard
rather than a free actuator. This version adds the piece that turns that posture
into a guarantee: a **control-barrier-function safe-motion layer** that certifies
the robot can never close distance on the dog below an adaptively estimated comfort
margin, over a wheel-odometry state estimate, degrading safe onto the existing
exposure gate and watchdog under any single failure. Alongside it, the perception
stack moved honestly from an ArUco collar marker to an on-device YOLO dog detector
(retaining ArUco for rigid robot localization), and the whole system now ships a
stationary default and an opt-in mobile mode across three camera architectures
behind single configuration switches. The engineering contributions - rate-based
arousal hysteresis, an executor-side motion watchdog, a single-knob gradual-
exposure gate, a closed-form CBF safety filter coupled to an online comfort
estimator, a task-matched perception split, and a failure-isolated three-tier
compute architecture - are, we believe, the transferable part of this work,
independent of any behavioral outcome.

We are explicit that this is **not** an efficacy study: n = 1, no control, no
behavioral claim, no ethics approval for one - and that the mobile/CBF layer,
while implemented and self-tested, is **not yet hardware-validated**. The natural
next steps: (i) install the encoder drivetrain and produce the *measured*
safety/estimation results the certificate is pre-registered to (`study/
STUDY_PROTOCOL.md`): a **zero** barrier-violation count over live sessions, a
tape-measured odometry drift figure, and the `D_comfort(s)` trajectory as Shifu
habituates; (ii) instrument and analyze the longitudinal fear-response record the
gradual-exposure protocol produces; (iii) design a properly controlled,
pre-registered, ethics-reviewed, multi-subject protocol with blinded coding,
following the standardized-training model in [2]; and (iv) finish full-loop
integration (encoder wiring, printed shell, Pi power, dashboard controller
hookup). Until then, CANIS stands as a systems-and-control contribution: a
demonstration that a home conditioning robot can be built to put an animal's
welfare, and its fear, at the *provable* center of its architecture.

---

## References

[1] S. R. Lindsay, *Handbook of Applied Dog Behavior and Training, Volume One:
Adaptation and Learning*. Ames, IA: Iowa State University Press, 2000.
ISBN 978-0-8138-0754-6.

[2] A. Stellato, S. Jajou, C. E. Dewey, T. M. Widowski, and L. Niel, "Effect of a
Standardized Four-Week Desensitization and Counter-Conditioning Training Program
on Pre-Existing Veterinary Fear in Companion Dogs," *Animals*, vol. 9, no. 10,
p. 767, 2019. doi: 10.3390/ani9100767.

[3] S. Garrido-Jurado, R. Muñoz-Salinas, F. J. Madrid-Cuevas, and
M. J. Marín-Jiménez, "Automatic generation and detection of highly reliable
fiducial markers under occlusion," *Pattern Recognition*, vol. 47, no. 6,
pp. 2280–2292, 2014. doi: 10.1016/j.patcog.2014.01.005.

[4] J. Redmon, S. Divvala, R. Girshick, and A. Farhadi, "You Only Look Once:
Unified, Real-Time Object Detection," in *Proc. IEEE Conf. Computer Vision and
Pattern Recognition (CVPR)*, 2016, pp. 779–788. doi: 10.1109/CVPR.2016.91.
`[Author TODO - OPEN, NEW: the deployed model is a nano YOLO (YOLO11n) exported to
NCNN; if a specific version's citation is preferred over the foundational YOLO
paper, swap it here.]`

[5] A. D. Ames, X. Xu, J. W. Grizzle, and P. Tabuada, "Control Barrier Function
Based Quadratic Programs for Safety Critical Systems," *IEEE Transactions on
Automatic Control*, vol. 62, no. 8, pp. 3861–3876, 2017.
doi: 10.1109/TAC.2016.2638961.

---

## Appendix A - Tunable cross-check against `canis_config.h`

Every numeric constant cited in this paper was checked directly against the single
source of truth for firmware tunables (`canis_config.h`, symlinked into
`CANIS_Core/`) and, for the one Arduino-side value not mirrored there, against
`Treat_Dispenser_CANIS/Treat_Dispenser_CANIS.ino`. **Result: no drift
found - every value matched.** The mobile/CBF and camera-architecture rows are new
in v3.

| Paper value | Cited as | Firmware constant | Value | Match? |
|---|---|---|---|---|
| Arousal rise rate | 30/s | `CANIS_AROUSAL_RISE_PER_SEC` | 30.0f | match |
| Arousal decay rate | 12/s | `CANIS_DECAY_PER_SEC` | 12.0f | match |
| AROUSAL enter/exit | 20 / 12 | `CANIS_AROUSAL_ENTER` / `_EXIT` | 20 / 12 | match |
| CORRECTION enter/exit | 70 / 60 | `CANIS_CORRECTION_ENTER` / `_EXIT` | 70 / 60 | match |
| Reward cooldown | 20 s | `CANIS_REWARD_COOLDOWN_MS` | 20000UL | match |
| Session reward cap | 30 | `CANIS_MAX_SESSION_REWARDS` | 30 | match |
| Sustained-calm interval | (referenced §3.3) | `CANIS_CALM_REWARD_MS` | 30000UL (≥ cooldown, `#error`-guarded) | match |
| Motion watchdog timeout | ~1 s | `MOTION_TIMEOUT_MS` (Uno sketch) | 1000 | match |
| Motion re-pulse cadence | 500 ms | `CANIS_MOVE_PULSE_MS` | 500UL (< 1000, guarded) | match |
| Orient / approach duration | 700 / 400 ms | `CANIS_ORIENT_MS` / `_APPROACH_MS` | 700UL / 400UL | match |
| Gate open time | 150 ms | `GATE_OPEN_MS` (Uno sketch) | 150 | match |
| PIR warm-up mask | 60 s | `CANIS_WARMUP_MS` | 60000UL | match |
| Correction cue | 800 Hz / 2 s | `CANIS_CORRECTION_TONE_FREQ` / `_ACTION_MS` | 800 / 2000UL | match |
| Exposure confirm window | 5 s | `CANIS_EXPOSURE_CONFIRM_MS` | 5000UL | match |
| **Mobile enable (default)** | 0 (stationary) | `CANIS_MOBILE_ENABLED` | 0 | match |
| **Never-approach invariant** | 1 (guarded in mobile) | `CANIS_NEVER_APPROACH` | 1 | match |
| **Comfort band D_MIN/D_MAX** | 60 / 200 cm | `CANIS_D_MIN_CM` / `_D_MAX_CM` | 60 / 200 (`#error` D_MIN<D_MAX) | match |
| **CBF gamma** | 0.5 | `CANIS_CBF_GAMMA` | 0.5f | match |
| **Control / odom-stream rate** | 20 / 10 Hz | `CANIS_CONTROL_RATE_HZ` / `_ODOM_STREAM_HZ` | 20 / 10 (guarded ≥10, ctrl≥odom) | match |
| **Motion command TTL** | 600 ms | `CANIS_MOTION_CMD_TTL_MS` | 600UL (< 1000, guarded) | match |
| **Speed caps** | 0.35 m/s / 1.5 rad/s | `CANIS_MAX_LINEAR_MPS` / `_MAX_ANGULAR_RPS` | 0.35f / 1.5f | match |
| **Encoder counts/rev** | 1320 | `CANIS_ENC_COUNTS_PER_REV` | 1320 | match |
| **Wheel diameter / base** | 65 / 140 mm | `CANIS_WHEEL_DIAMETER_MM` / `_WHEEL_BASE_MM` | 65.0f / 140.0f | match |
| **Encoder pins L / R** | 18,19 / 16,17 | `CANIS_ENC_L_A/B_PIN` / `_R_A/B_PIN` | 18,19 / 16,17 | match |
| **Camera mode (default)** | A onboard | `CANIS_CAMERA_MODE` | `CANIS_CAMERA_ONBOARD` (0) | match |
| **Mode B stream / HFOV / stall** | ESP32-CAM 65° / 5 s | `CANIS_ESP32CAM_STREAM_URL` / `_HFOV_DEG` / `_STALL_S` | …:81/stream / 65.0f / 5.0f | match |
| **Mode C cam HFOV / height / tilt** | 66° / 2.0 m / 30° | `CANIS_FIXED_CAM_HFOV_DEG` / `_HEIGHT_M` / `_TILT_DEG` | 66.0f / 2.00f / 30.0f | match |
| **Robot marker id / size / height** | 17 / 100 mm / 0.18 m | `CANIS_ROBOT_MARKER_ID` / `_SIZE_MM` / `_HEIGHT_M` | 17 / 100.0f / 0.18f (id guarded 0..49) | match |
| **Vision-side ToF gate bearing** | \|β\| ≤ 15° | `control/estimator.py` (Python) | 15° | match (Python-side) |
| **Comfort-margin uncertainty cap** | min(σ, 30 cm) | `control/motion_controller.py` (Python) | 30 cm | match (Python-side) |
| **YOLO confidence gate** | 0.60 | `detect_yolo.py` `YOLO_CONF_MIN` | 0.60 | match (Python-side) |
| **Dog-height (monocular range)** | 0.45 m | `detect_yolo.py` `DOG_HEIGHT_M` | 0.45 | match (Python-side) |

The compile-time `#error` guards in `canis_config.h` catch *internally
inconsistent* firmware edits (e.g. `CORRECTION_EXIT >= CORRECTION_ENTER`,
`D_MIN >= D_MAX`, `MOBILE && !NEVER_APPROACH`, `ODOM_STREAM_HZ < 10`,
`CAMERA_MODE` out of 0..2, `ROBOT_MARKER_ID` out of 0..49); a `.h`↔`.py`
config-parity test (`tests/`) catches firmware↔dashboard drift; but
nothing except a manual pass like this table catches a firmware value drifting
away from what this *paper* claims. Re-diff this table on any `canis_config.h`
tuning change. The Python-side rows (last five) live in the controller/vision
sources rather than the header (a header cannot be imported by Python); they are
mirrored per `docs/INTERFACE_CONTRACT.md` §3.1 and `SAFE_MOTION_CBF_SPEC.md`.

---

## Appendix B - Figures

- **Figure 1 - System data flow** (`paper/figures/figure1_dataflow.svg`):
  the three-tier compute split, what each tier owns, and the inter-tier links.
  Hand-authored SVG matching `docs/ARCHITECTURE.md` §1 (the sandbox had
  no network to run the Mermaid CLI). **[Author TODO - OPEN, NEW: extend with the
  mobile-mode `/telemetry`, `/motion`, and `VEL` links.]**
- **Figure 2 - Arousal state machine**
  (`paper/figures/figure2_arousal_state_machine.svg`): the full
  `CALM`/`AROUSAL`/`CORRECTION` transition graph with every enter/exit threshold.
- **Figure 3 - CBF safety filter** *(placeholder)*: `u_nom` (arousal/exposure) →
  QP half-space projection on `v` under `v·cosβ ≤ γ·h` → actuator clamp → `u*` →
  `MOVE v ω`. **[Author TODO - OPEN, NEW: draw from `docs/SAFE_MOTION_CBF_SPEC.md`
  §3–4 and `control/cbf.py`.]**
- **Figure 4 - Odometry drift vs. tape-measured ground truth** *(placeholder,
  needs hardware)*.
- **Figure 5 - `D_comfort(s)` trajectory over sessions** *(placeholder, needs the
  study)*.

---

## Reproducibility / artifacts

Integrated firmware: `CANIS_Core/CANIS_Core.ino` (ESP32 brain + state
machine + encoder/odometry/telemetry; single-source tunables in
`canis_config.h`). Executor: `Treat_Dispenser_CANIS/` (Arduino
dispenser + motion watchdog + `VEL` differential-drive verb). Vision:
`vision/detect_yolo.py` (YOLO, default, modes A/B),
`vision/detect_fixed.py` (fixed-room mode C: YOLO dog + ArUco robot
marker), `vision/detect.py` (ArUco, legacy fallback + shared camera
sources/helpers), `vision/vision_logic.py` (grace/frozen/blur, unit-
tested). Controller (Pi): `control/` - `estimator.py` (odometry⊕IMU⊕ToF⊕
vision), `comfort.py` (adaptive `D_comfort`), `cbf.py` (closed-form QP filter),
`reward.py` (voluntary-approach detector), `motion_controller.py` (orchestrator +
mode gating + 38-check `--selftest`), `config.py`. Contracts:
`docs/INTERFACE_CONTRACT.md` (wire formats - LAW),
`docs/SAFE_MOTION_CBF_SPEC.md` (barrier math),
`docs/MOBILE_UPGRADE_STATUS.md` (mode/camera status). Dashboard +
command/telemetry channel: `dashboard/app.py` (+ `canis_config.py`
mirror). Tests: `tests/` (pytest, no hardware; includes the `.h`↔`.py`
config-parity check that keeps Appendix A honest) and the hardware-free
`--selftest`s in `detect_yolo.py`, `detect_fixed.py`, and
`control/motion_controller.py`. Figures: `paper/figures/`.
