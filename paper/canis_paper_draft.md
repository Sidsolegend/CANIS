# CANIS: Design of a Humane Robotic Conditioning System for a Fearful Dog, with Preliminary Single-Subject Observations

**Draft v1** — a systems / design paper. This describes the design and
implementation of a home conditioning robot and reports *preliminary,
single-subject* observations. It is **not** an efficacy study and makes **no
behavioral-outcome claim**. All architecture and firmware details below are
grounded in the committed source (`CANIS_Core/CANIS_Core.ino`,
`dashboard/app.py`, `vision/detect.py`, `Treat_Dispenser_CANIS/`).

> **Author TODOs before submission:** (1) add author/affiliation; (2) fill real
> citations at `[REF]`; (3) export figures (the data-flow diagram, the arousal
> state-machine timeline, a photo of the deployed unit — `IMG_0297.jpeg` /
> `IMG_0298.jpeg`); (4) confirm the exposure-level log format and attach the
> longitudinal fear-response sheet once enough sessions exist; (5) confirm all
> tunable values against the *current* firmware before the camera-ready, since
> the README states they are still being tuned against the subject;
> (6) obtain and cite an institutional/veterinary-behaviorist review if the paper
> is ever reframed toward an efficacy claim (see §8).

---

## Abstract

We present CANIS, a home robot that applies classical (Pavlovian) conditioning
to address unwanted behaviors in a young dog — chewing forbidden objects,
barking, climbing furniture, and roaming — by **rewarding the dog for leaving a
forbidden zone rather than punishing it for entering**. The design is shaped by
one hard constraint: the subject dog is *afraid of moving objects*, so a robot
that corrects by charging at the animal would be actively harmful. CANIS
therefore treats robot motion as a controlled, gradually-introduced variable
gated behind an explicit **exposure level**, defaulting to no movement at all.
The contribution of this paper is a **system design**: (i) a reward-on-leaving
conditioning model realized as an arousal-driven three-state machine with
rate-based hysteresis; (ii) a fail-safe motion architecture in which a "dumb"
real-time executor stops the robot within ~1 s of losing its command link;
(iii) a gradual-exposure control gate that makes welfare a first-class,
single-knob setting; (iv) a pragmatic perception choice — ArUco fiducial markers
over a learned detector (e.g. YOLO) — justified by reliability and compute
budget; and (v) a three-tier compute split (real-time MCU, coordinating MCU with
networking, and a vision/dashboard host) matched to each device's strengths. We
report **preliminary single-subject observations** (one dog, "Shifu"; n = 1; no
control group; no ethics approval for a behavioral claim) and are explicit that
these are engineering-validation observations of the *system*, not evidence that
the intervention changes behavior. We include an ethics and animal-welfare
statement and enumerate the limitations that separate this work from an efficacy
study.

---

## 1. Introduction

Companion-animal behavior problems are common, and many owners cannot supply the
consistent, precisely-timed reinforcement that effective conditioning requires.
An always-present device that delivers a consistent marker-and-reward at the
right moment is therefore attractive. The dominant risk, however, is that an
automated "corrector" becomes an aversive, fear-inducing presence — especially
for an animal already sensitive to movement.

CANIS is a home robot built for exactly this situation. The subject is a young
Goldendoodle ("Shifu") who exhibits several target behaviors (eating things he
should not, barking, climbing furniture, roaming) *and* is measurably afraid of
moving objects. This second fact reframes the whole problem: the system cannot
treat robot motion as free. Instead of punishing the dog for entering a
forbidden zone — which for a fearful dog means pairing the zone with a scary,
approaching machine — CANIS **marks and rewards the dog for leaving**, and it
introduces its own motion *gradually*, over weeks or months, only as the dog's
logged fear response shows tolerance.

This paper is deliberately scoped as a **systems and design** contribution. We
do **not** claim that CANIS reduces the target behaviors; establishing that
would require a controlled, multi-subject, ethically-reviewed study that we have
not run (§8). What we do claim is a coherent, safety-first design for a humane
conditioning robot, and we report preliminary observations that validate the
*engineering* — that the closed loop behaves as designed on real hardware with a
real dog in the room.

**Contributions.**
1. A **reward-on-leaving conditioning model** expressed as an arousal-driven
   three-state machine (`CALM` / `AROUSAL` / `CORRECTION`) with rate-based
   hysteresis, so that transient detections do not trigger interventions and the
   *act of disengaging* is what earns reinforcement.
2. A **fail-safe motion architecture**: a real-time microcontroller runs a
   motion watchdog and halts within ~1 s if its command link goes silent, so a
   crash or a loose cable cannot leave the robot moving toward a fearful animal.
3. A **gradual-exposure gate** (`EXPOSURE_LEVEL`) that makes animal welfare a
   single, explicit, default-safe control knob, decoupling *what the robot
   decides* from *how much it is allowed to physically move*.
4. A pragmatic **ArUco-over-YOLO** perception choice for a resource-constrained,
   reliability-critical home deployment, with the design rationale made explicit.
5. A **three-tier compute split** (real-time executor MCU / coordinating MCU with
   WiFi / vision-and-dashboard host) with a rollover-safe, back-off-hardened link
   protocol between tiers.

---

## 2. Related work

`[REF]` Automated / robotic animal training and enrichment devices; treat-
dispensing feeders with app control; positive-reinforcement (operant/classical)
conditioning and the role of marker timing; differential reinforcement of
alternative behavior (DRA/DRO) as a humane alternative to positive punishment;
fear and stress in companion animals and desensitization / counter-conditioning
protocols; fiducial-marker detection (ArUco/AprilTag) vs. learned object
detectors (YOLO family) on edge devices; watchdog / fail-safe patterns in mobile
robots; multi-tier embedded system architectures (MCU + SBC). *(To be filled
with real citations, including at least one veterinary-behavior reference for the
reward-on-leaving / counter-conditioning framing and one animal-research-ethics
reference for §8.)*

---

## 3. System design

### 3.1 Design constraints
Three constraints drove every subsequent decision:

- **C1 — Do no harm to a fearful animal.** The subject is afraid of moving
  objects. Robot motion is therefore a *hazard to be rationed*, not a free
  actuator. This makes "the robot must be able to stop, reliably and quickly" a
  safety requirement, and "the robot must be able to *not move at all* while
  still functioning" a first-class operating mode.
- **C2 — Reward leaving, never punish entering.** For a fearful dog, punishing
  zone entry pairs the zone (and the robot) with fear. CANIS instead reinforces
  the desired alternative behavior — disengaging from the zone.
- **C3 — Empirical, tunable thresholds.** Every arousal/timing threshold is
  meant to be tuned against the individual dog's observed behavior, so the design
  keeps them as named constants co-located in firmware and in the dashboard
  simulator, and explicitly documents that they are provisional.

### 3.2 Three-tier compute split
CANIS distributes work across three devices, each matched to what it does best:

| Device | Role | Why here |
|--------|------|----------|
| **Arduino Uno** ("limbs") | Real-time executor: motor + dispenser servo + marker buzzer. | Precise, jitter-free 5 V servo/motor timing; kept deliberately "dumb" and reliable. |
| **ESP32** ("brain") | Reads sensors, runs the arousal state machine, *decides*, talks both ways. | Enough compute for the FSM plus WiFi to coordinate the other two tiers. |
| **Raspberry Pi 5** ("eyes + face") | OpenCV vision + Flask dashboard. | Vision and a web UI are beyond either MCU; the Pi also owns the wall clock. |

The split also isolates failure domains: the real-time executor cannot be
starved by network stalls (§3.4), and the decision tier never blocks on vision
(it *polls* the Pi rather than being driven synchronously by it).

### 3.3 The arousal state machine (conditioning core)
The behavioral model is realized as three states driven by a scalar **arousal**
level in `[0, 100]`:

| State | Meaning | Behavior |
|-------|---------|----------|
| `CALM` | Baseline, settled | Rewards *sustained* calm (after a warm-up, rate-limited). |
| `AROUSAL` | Something detected, watching | **Monitor only — no intervention.** |
| `CORRECTION` | Bad behavior confirmed | Emits an 800 Hz "disengage" cue every 2 s and (only if exposure allows) orients toward the zone. |

Arousal is **rate-driven**, which gives the system hysteresis for free. While
*any* detection is active — local PIR/ultrasonic **or** a remote vision call from
the Pi — arousal rises at `+AROUSAL_RISE_PER_SEC` (30/s); it *always* decays at
`DECAY_PER_SEC` (12/s). Net presence therefore climbs at +18/s and absence
settles at −12/s. Crossing `AROUSAL_ENTER` (20) enters `AROUSAL`; crossing
`CORRECTION_ENTER` (70) enters `CORRECTION`. Because entry and the eventual
return to `CALM` are separated by an accumulate-and-decay integrator rather than
a single threshold, brief spurious detections cannot flip the system into a
correction, and a dog that genuinely lingers is held in the aroused/correcting
band (a confirmed detection keeps arousal up while it lasts, so a lingering dog
is never wrongly "rewarded for leaving").

The **key design idea** is the transition rule: dropping from `AROUSAL` or
`CORRECTION` back to `CALM` means the dog disengaged — and *that* transition
fires the reward. CANIS rewards **leaving**, never punishes entering (C2).

Reward delivery is rate-limited for safety and hardware protection: a 20 s
cooldown (`REWARD_COOLDOWN_MS`) between any two treats and a hard per-session cap
(`MAX_SESSION_REWARDS` = 30) so the hopper cannot empty and so in/out "bouncing"
at a zone edge cannot rapid-fire treats.

### 3.4 Fail-safe motion watchdog
Because motion is a hazard (C1), the executor tier runs a **motion watchdog**:
the Arduino stops the motors if it does not receive a fresh motion command within
`MOTION_TIMEOUT_MS` (~1 s). The ESP32 correspondingly **re-pulses** any active
motion every `MOVE_PULSE_MS` (500 ms) for as long as it intends the robot to
move (`serviceMotion()`), and stops re-pulsing the instant the intent ends. The
consequence is that any failure that silences the ESP32 → Arduino link — a crash,
a reset, a loose wire — causes the robot to **coast to a stop within ~1 s** on
its own, rather than continuing to execute the last command toward the dog.
Correction motions are also *bounded in duration* (`ORIENT_MS` = 700 ms, optional
`APPROACH_MS` = 400 ms) and are halted immediately on leaving `CORRECTION`
(without waiting for the watchdog), since a dog that disengaged must not be
chased.

### 3.5 Gradual-exposure architecture
Welfare is concentrated into a single, explicit gate, `EXPOSURE_LEVEL`, described
in-code as "the welfare knob":

- **Level 0 (default):** no movement at all — correction is *buzzer-only*.
- **Level 1:** orient only — pivot to face the zone, never approach.
- **Level 2:** orient plus a brief, gentle forward approach.

The decision logic (state machine) is identical at every level; the exposure
gate sits between "the robot decided to correct" and "the robot physically
moves." The intended protocol is to raise the level by **one step at a time**,
never skipping, and only after the dog's *logged fear response* shows comfort at
the current level. This is the mechanism by which CANIS realizes gradual
desensitization: the same closed loop runs from day one, but its physical
intrusiveness is dialed up slowly under human supervision. The longitudinal
fear-response record produced by this process is, per the project framing, the
actual scientific artifact of interest (future work; §9).

### 3.6 Perception: ArUco over YOLO (a pragmatism argument)
Detection has two independent sources. Locally, the ESP32 fuses a PIR motion
sensor with **three ultrasonic rangefinders** (left/centre/right), fired
sequentially to avoid echo cross-talk; the trio doubles as a coarse local bearing
toward the dog. Remotely, the Pi runs vision and posts a *zone* to the dashboard,
which the ESP32 polls as a remote detection.

For the vision stage we deliberately chose **ArUco fiducial markers** (a
`4x4_50` dictionary marker on the dog's collar) over a learned object detector
such as YOLO. The rationale:

- **Reliability over generality.** The task is not "find any dog in any pose"; it
  is "is *this* dog in *this* room." A fiducial gives a near-deterministic,
  low-false-positive answer, which matters because a false correction is
  actively harmful to a fearful animal (C1).
- **Compute and latency budget.** Marker detection runs comfortably on the Pi
  alongside the dashboard, with frame-skipping (`PROCESS_EVERY`) and a
  Laplacian-variance blur guard to reject motion-blurred frames; a full CNN
  detector would consume far more of the Pi's budget for no reliability gain on a
  single, cooperative, marker-wearing subject.
- **Identity for free.** The marker *is* the dog's identity, so other people or
  pets in frame do not trigger the system.
- **Model:** one camera watches one room, so the camera's location *is* the zone.
  A directional suffix (`-left`/`-center`/`-right`) from the marker's position in
  the frame gives a rough orientation hint; the robot's own ultrasonics remain the
  authoritative bearing.

We treat this as a *pragmatism* result rather than a claim that fiducials
dominate learned detectors in general: for a reliability-critical,
single-subject, compute-constrained home deployment, the fiducial is the correct
engineering choice.

### 3.7 Inter-tier protocol and robustness
The ESP32 pushes state to the Pi (`POST /ingest`, 1 Hz plus on-change) and polls
for vision commands (`GET /command`, 1 Hz). Both share a **rollover-safe
back-off**: if the Pi is unreachable, the ESP32 stops hammering it for 5 s and
keeps its control loop snappy, and all `millis()` comparisons are written to
survive counter rollover. The Pi supplies what the ESP32 lacks — a **wall
clock** — by watching `reward_count` tick and stamping `last_reward_time` with
its own time. The dashboard also falls back to a built-in simulator (mirroring
the firmware logic) if no ESP32 data has arrived in 5 s, so a demo is never
blank, with a footer that states whether the view is *live* or *simulated*.

---

## 4. Methods

### 4.1 Hardware bring-up and per-subsystem validation
The system was brought up in phases, each hardware-tested in isolation before
integration:

- **Mobility.** L298N differential drive on a metal chassis, both channels
  PWM-matched, all five motion commands verified; run on a 6 V AA pack. Speeds
  were set high (200/220) for bring-up and are intended to be dialed *down* for
  exposure sessions.
- **Dispenser.** A button/serial-triggered servo gate with a narrowed tube
  "throat" (one treat at the gate at a time); `GATE_OPEN_MS` is the main
  reliability knob. Bench-tested.
- **ESP32 ↔ Arduino serial.** One-way `DISPENSE`/`FORWARD`/`STOP`/… link, with a
  1 kΩ/2 kΩ divider (or level shifter) protecting the 3.3 V ESP32 RX from the
  5 V Arduino TX, and `SoftwareSerial` on the Uno.
- **Sensors.** PIR (with a 60 s warm-up mask) and HC-SR04 ultrasonic (1 kΩ on
  ECHO) proven; expanded to three ultrasonics in firmware, with the 3-sensor
  positional test deferred to post-print (positions come from the printed shell).
- **State machine.** Real outputs — dispense, correction cue, reward
  cooldown/cap, and *watchdog-safe* pulsed orient — verified on the integrated
  `CANIS_Core` firmware.
- **Vision.** End-to-end on hardware: camera → OpenCV/ArUco detects marker ID 0 →
  directional zone posted → dashboard serves it to the polling ESP32.
- **Dashboard.** Live ESP32→Pi link with simulator fallback; auto-starts on boot
  (systemd).

### 4.2 Exposure protocol (as designed)
Gradual exposure runs on its own weeks/months calendar, in parallel with the
build, and starts as soon as the robot can move at all. The protocol: begin at
`EXPOSURE_LEVEL = 0` (buzzer-only), observe and log the dog's fear response over
repeated sessions, and advance by exactly one level only when the log shows
comfort at the current level. `[Author TODO: specify the fear-response coding
scheme — e.g. latency to approach, tail/ear posture, avoidance distance,
willingness to take the treat — and the pre-registered advance/hold/retreat
criteria, before any of this is used to support a claim.]`

### 4.3 What we measured, and what we did not
We collected **engineering-validation** observations: whether each subsystem
behaves as specified, whether the closed loop makes the intended transitions and
rewards on-leaving, and whether the fail-safes fire. We did **not** run a
controlled behavioral trial, and we did not collect the data that a
behavior-change claim would require (baseline behavior rates, a control
condition, blinded coding, multiple subjects). This scoping is deliberate (§8).

---

## 5. Preliminary observations (single subject, n = 1)

> **These are single-subject, uncontrolled, engineering-validation observations.
> They are not evidence of behavioral efficacy.** Read §7 and §8 first.

`[Author TODO: replace the bracketed placeholders below with the actual logged
values/anecdotes; keep every statement descriptive and non-causal.]`

- **The closed loop behaves as designed with a real dog in the room.** With local
  sensors and the Pi vision path both feeding arousal, the system entered
  `AROUSAL` on confirmed presence, escalated to `CORRECTION` when presence
  persisted past the threshold, and delivered a treat on the return to `CALM`
  (reward-on-leaving), subject to the cooldown/cap. `[Add: qualitative note on
  timing/feel.]`
- **The hysteresis suppressed spurious corrections.** Brief/transient detections
  raised arousal but decayed below `AROUSAL_ENTER` before reaching
  `CORRECTION_ENTER`, so no intervention fired. `[Add: observed count of
  transient detections that did not escalate.]`
- **The fail-safe fired as intended.** In deliberate link-drop tests (unplugging
  the ESP32→Arduino line mid-motion), the robot stopped within ~1 s without an
  explicit STOP. `[Add: measured stop latency.]`
- **Exposure Level 0 was used throughout the initial sessions.** Consistent with
  the welfare-first protocol, early sessions ran buzzer-only; `[Add: whether and
  when Level 1 was reached, and the fear-response observations that gated it.]`
- **Vision reliability.** ArUco marker detection produced `[Add: qualitative
  false-positive/miss notes]`; the blur guard and frame-skip kept the Pi
  responsive alongside the dashboard.

We report these as *directional, single-subject* engineering observations only.

---

## 6. Discussion

### 6.1 Why "reward leaving" is the right primitive for a fearful dog
A conventional deterrent robot creates an aversive association between the zone
(and the robot) and fear — the opposite of what a movement-sensitive dog needs.
By reinforcing disengagement instead, CANIS aligns the training signal with the
desired behavior and keeps the robot's salient events *positive* (a treat marker)
rather than *threatening*. The arousal integrator is what makes this
implementable: it defines "left the zone" as a *sustained decay back to CALM*,
not an instantaneous sensor edge, so the reward lands on genuine disengagement.

### 6.2 Safety as an architectural property, not a feature
The two hazards specific to this deployment — an unwanted correction and a robot
that keeps moving toward a scared animal — are addressed structurally: the former
by hysteresis plus the monitor-only `AROUSAL` state (the system watches before it
ever acts), and the latter by the executor-side watchdog plus bounded, gated
motion. Neither depends on the decision tier being healthy, which is the point: a
crashed brain yields a *stopped* robot.

### 6.3 The exposure gate decouples policy from intrusiveness
Separating `EXPOSURE_LEVEL` from the state machine means the training policy can
be developed and validated once, while the physical intrusiveness is titrated
slowly and reversibly. This is both a welfare mechanism and a clean engineering
boundary: the same decisions drive a buzzer-only device and a moving one.

### 6.4 On the ArUco choice
The fiducial trades generality for determinism, identity, and compute headroom —
all of which serve C1 (a false correction is harmful). A learned detector would
be the right call if the deployment had to generalize across dogs, drop the
collar marker, or estimate pose; for this subject and this reliability budget, it
would add risk and cost without benefit.

---

## 7. Limitations

- **n = 1, no control group.** All observations are from one dog in one home.
  There is no baseline-versus-intervention comparison and no control condition,
  so nothing here can distinguish a system effect from ordinary variation,
  maturation, or owner effects.
- **No efficacy claim, and no basis for one.** We did not measure target-behavior
  rates before/after, did not blind any coding, and did not pre-register outcome
  criteria. The observations validate the *system*, not the *intervention*.
- **Thresholds are provisional.** Arousal rise/decay, entry thresholds, cooldowns,
  and exposure timings are empirical and still being tuned against the subject;
  the values in this draft should be re-confirmed against the current firmware.
- **Sensing is coarse.** PIR + ultrasonic + a single-room fiducial give
  presence-in-a-zone, not fine-grained behavior recognition; the system cannot
  distinguish *which* unwanted behavior is occurring, only zone occupancy.
- **Bearing is approximate.** The camera-frame left/right suffix is a rough hint,
  not the robot's true heading; only the onboard ultrasonics give an authoritative
  local bearing, and even that is coarse.
- **Integration not fully closed.** At the time of writing, the full-loop run with
  the final 3-sensor wiring and the printed shell is pending, and the Pi's 5 V
  power dial-in is unfinished.
- **Generality unknown.** The design is tuned to one fearful dog; whether it
  transfers to dogs with different temperaments (or to non-fearful dogs, where the
  motion constraints could be relaxed) is untested.

---

## 8. Ethics and animal-welfare statement

This project involves a companion animal (a privately-owned dog living in the
author's home), and its central design constraint is the animal's welfare.

- **Framing and scope.** This paper is a **systems/design** report with
  preliminary single-subject engineering observations. It makes **no claim** that
  the device changes the dog's behavior. We explicitly did **not** conduct a
  behavioral-efficacy study, and we did **not** obtain institutional animal-ethics
  approval, because we are not reporting a behavioral result. `[Author TODO: if
  the work is ever reframed toward an efficacy claim, obtain prior review from an
  appropriate animal-research ethics body and/or a qualified veterinary
  behaviorist, and add the approval reference here.]`
- **Welfare-first design.** The system defaults to **no movement** (exposure
  Level 0) and uses only a mild auditory cue and a food reward; it reinforces a
  desired alternative behavior (leaving) rather than applying positive
  punishment. Motion is introduced gradually, one small step at a time, gated on
  the dog's observed fear response, and is reversible.
- **Physical safety.** A hardware motion watchdog stops the robot within ~1 s of
  any command-link loss; correction motions are duration-bounded and are halted
  immediately when the dog disengages. Speeds are dialed down for exposure
  sessions. The dog is supervised during sessions. `[Author TODO: state the
  supervision and stop-session criteria explicitly — e.g. session ends on any
  sign of distress.]`
- **Reward safety.** Per-session reward caps and cooldowns prevent over-feeding
  and hopper depletion; `[Author TODO: confirm the daily treat budget is within a
  veterinarian-appropriate amount for the subject's size/diet.]`
- **Data.** The system records inertial/ultrasonic/PIR counters, arousal/state
  logs, and marker detections; the camera is used only for on-device fiducial
  detection (a zone label is posted, not imagery). `[Author TODO: confirm no
  video is stored, or state the retention/handling policy if any frames are
  saved, e.g. for the `--snap` diagnostic.]`
- **Consent.** The animal's owner (the author) consents to the dog's
  participation and can end any session at will.

---

## 9. Conclusion and future work

CANIS is a design for a humane conditioning robot for a fear-sensitive dog, built
around a single behavioral primitive — *reward the dog for leaving, never punish
it for entering* — and a safety posture in which robot motion is a rationed
hazard rather than a free actuator. The engineering contributions (rate-based
arousal hysteresis, an executor-side motion watchdog, a single-knob gradual-
exposure gate, a reliability-first ArUco perception stage, and a failure-isolated
three-tier compute split) are, we believe, the transferable part of this work,
independent of any behavioral outcome.

We are explicit that this is **not** an efficacy study: n = 1, no control, no
behavioral claim, and no ethics approval for one. The natural next steps are all
about turning the *system* into a position from which a *claim* could eventually,
and ethically, be made: (i) instrument and analyze the longitudinal
fear-response record that the gradual-exposure protocol produces (the project's
intended scientific artifact); (ii) design a properly controlled, pre-registered,
ethics-reviewed, multi-subject protocol with blinded behavior coding; and
(iii) finish full-loop integration (3-sensor wiring, printed shell, Pi power) so
the deployment is stable enough to support such a study. Until then, CANIS stands
as a systems contribution: a demonstration that a home conditioning robot can be
built to put an animal's welfare, and its fear, at the center of its
architecture.

---

## Reproducibility / artifacts

Integrated firmware: `CANIS_Core/CANIS_Core.ino` (ESP32 brain + state machine).
Executor: `Treat_Dispenser_CANIS/` (Arduino dispenser + motion watchdog).
Dashboard + command channel: `dashboard/app.py` (+ `templates/index.html`,
`canis-dashboard.service`). Vision: `vision/detect.py` (+ `vision/SETUP.md`).
Wiring / bring-up: `hardware/WIRING.md`, `hardware/TESTING.md`. CAD:
`hardware/cad/`. Reference sketches: `State_Machine_CANIS/`,
`State_Machine_WiFi/`, `Sensors_Test/`. Tunables are co-located in the `.ino`
files and `dashboard/app.py` and are intended to be kept in sync.
