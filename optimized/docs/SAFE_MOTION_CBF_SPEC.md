# CANIS — Formal Safe-Motion Layer (Control-Barrier-Function spec)

**Status:** SPEC — Pi-side software implemented (`optimized/control/`,
`--selftest` passing), hardware bring-up pending (encoder gearmotors to be wired;
`CANIS_MOBILE_ENABLED` still `0` by default). This document specifies the
*upgrade* that turns CANIS's existing discrete "never-approach" welfare rule into
a **formally certified** safe-motion layer, enabled by the new wheel-encoder
drivetrain. Open decision #1 (identity sensor) is now **resolved** (§9).

**Relationship to existing docs — read these first, this builds on them, it
does not replace them:**
- `docs/SAFETY.md` — the enumerated fail-safe layers. This spec adds **one new
  layer** (a continuous distance barrier) *above* the existing exposure gate;
  it does not remove any existing fail-safe.
- `CANIS_Core/canis_config.h` — single source of truth for tunables. Every
  value cited here is either from that file (marked *[existing]*) or is a new
  tunable this spec proposes (marked *[new]*).
- `paper/canis_paper_draft_v2.md` — the systems paper. This layer is the
  intended **§ "safe motion"** contribution; it slots into the existing
  arousal/exposure architecture.
- `study/STUDY_PROTOCOL.md` — validation. §8 below adds the pre-registered
  metrics that test this layer specifically.

---

## 1. Motivation — why formalize what already works

CANIS already refuses to charge the dog. Two existing mechanisms do it:

- **`CANIS_EXPOSURE_DEFAULT = 0`** *[existing]* — motion is off by default;
  levels 1 (orient/pivot only) and 2 (orient + 400 ms nudge) are opt-in.
- **`[OPT-8]` never-approach invariant** *[existing]* — at exposure < 2 a
  `FORWARD` bearing is rewritten to an in-place pivot, so the robot "can never
  close distance below level 2."

These are correct but **discrete and open-loop**: they gate *whether* motion is
allowed by a mode switch, not *how close the robot may be* as a continuous,
measured quantity. There is no feedback signal that says "the robot is now
40 cm from Shifu and must not get closer." The robot does not know its own
distance to the dog as a controlled variable, because it has no odometry and
the distance is never in the loop.

The CMU-caliber contribution is to replace "a mode that forbids forward motion"
with **"a control barrier function that certifies the robot-to-dog distance can
never drop below the dog's comfort margin, for *any* commanded behavior."** This
is a *provable forward-invariance* property, not a rule we hope holds — and it
degrades gracefully back onto the existing exposure gate and watchdog if any
sensor drops out.

The new drivetrain (2× encoder gearmotors, `docs`/parts list) is what makes
this possible: without wheel odometry there is no robot-state estimate to base
a barrier certificate on.

---

## 2. State estimate

New hardware in this loop: **quadrature wheel encoders** (read on the ESP32
hardware pulse counters, PCNT), the existing **IMU** (heading), the existing
**VL53LX ToF** (robot→dog range), and the existing **camera** (dog bearing;
a learned **YOLO** detector — §9 decision #1, now resolved).

State vector (planar, robot frame):

```
x = [ p_x, p_y, θ, v, ω, d_rd, β ]
      └ robot pose ┘ └ vel ┘ └ dog ┘
```

- `p_x, p_y, θ` — robot pose from **wheel-odometry ⊕ IMU** (complementary or
  EKF fusion: encoders give translation + a yaw estimate; the IMU corrects yaw
  drift, which differential-drive odometry accumulates fastest).
- `v, ω` — body linear/angular velocity, differentiated from encoder counts
  (signed — this is why quadrature, not single-channel, encoders are required).
- `d_rd` — **robot-to-dog distance**, primary signal from the **VL53LX ToF**,
  gated by the camera so it ranges *the dog* and not a wall (bearing from
  vision selects whether the ToF reading is "the dog" or background).
- `β` — bearing to the dog (camera).

**Estimator:** a lightweight EKF on the Pi (the ESP32 streams raw encoder +
IMU + ToF at ≥10 Hz; the Pi fuses and runs the barrier QP). Start with a
constant-velocity motion model; the measurement models are linear-ish, so even
a complementary filter + median-gated ToF is a defensible v1. Report odometry
error against a tape-measured ground-truth run as a validation figure.

---

## 3. Safety set and the barrier function

Define the **comfort set** — the set of states we certify the robot stays in:

```
h(x) = d_rd − D_comfort(s)      ≥ 0          (stay at least D_comfort from the dog)
```

where `D_comfort(s)` is the dog's current comfort margin (§5), a function of an
estimated stress state `s`. `h(x) ≥ 0` is the safe set `C`; `h(x) = 0` is the
boundary (robot exactly at the comfort margin); `h < 0` is a comfort violation
and must be **unreachable**.

**Control barrier function condition** (forward invariance of `C`): choose an
extended class-𝒦 function `α` (start linear, `α(h) = γ·h`, `γ > 0` *[new]*) and
require every commanded input `u = [v, ω]` to satisfy

```
ḣ(x, u)  ≥  −α(h(x))               (CBF constraint)
```

Intuitively: the closer the robot is to the comfort boundary (`h → 0`), the
harder the constraint forbids any input that reduces `d_rd`. At the boundary
(`h = 0`) only inputs that *increase or hold* distance are admissible — the
robot can retreat or hold, never advance. This is the continuous generalization
of `[OPT-8]`: OPT-8 is the degenerate case `D_comfort = ∞ below exposure 2`.

Because `ḣ` depends on the dog's motion too (the dog can close the gap on his
own), the certificate is over the robot's *contribution* to `ḣ`: the robot's
commanded velocity may never make `d_rd` decrease. A decrease in `d_rd` is then,
by construction, attributable **only to the dog choosing to approach** — which
is exactly the event we reward (§4).

---

## 4. Nominal controller (the "reference" the barrier filters)

The barrier is a *safety filter*, not the whole behavior. Underneath it runs the
existing arousal/exposure logic, unchanged, producing a *desired* input
`u_nom`:

- Arousal state machine (`CALM / AROUSAL / CORRECTION`, hysteresis 20/60/70)
  *[existing]* decides *intent*.
- Exposure level *[existing]* still gates what class of motion is even proposed
  (0 = none, 1 = pivot, 2 = pivot + nudge).
- Heading control (pivot to face the dog) is a separate channel and is **not**
  distance-reducing, so it passes the barrier untouched — *facing ≠ approaching*.
- **Reward-on-approach:** when the estimator sees `d_rd` decrease while the
  robot's own commanded `v ≤ 0` (holding/retreating), that is a voluntary
  approach → marker (2000 Hz) + one treat, subject to `REWARD_COOLDOWN_MS`
  *[existing, 20 s]*.

The safety filter is the **CBF quadratic program**, solved each control tick:

```
u* = argmin_u  ‖u − u_nom‖²
     subject to   ḣ(x, u) ≥ −γ·h(x)          (comfort barrier)
                  u ∈ U   (actuator limits: |v| ≤ v_max, |ω| ≤ ω_max)
```

`u*` is the closest admissible command to what the behavior *wanted*. A 2-input
QP with one linear inequality has a closed-form projection — no heavyweight
solver needed; it runs comfortably on the Pi at the control rate.

---

## 5. Adaptive comfort margin `D_comfort(s)` — the estimation contribution

A fixed standoff is a weak claim. Instead, estimate the dog's comfort margin
online and feed it into the barrier, so the safe set *tightens when Shifu is
stressed and relaxes as he habituates*:

- Observation signals: approach/retreat dynamics (sign of `ḋ_rd`), dwell time
  near the robot, and the existing **arousal** estimate.
- Recursive update (Bayesian / low-pass) of a latent comfort state `s`, mapped
  to `D_comfort(s) ∈ [D_min, D_max]` *[new, e.g. 0.6–2.0 m]*.
- Interpretation: if Shifu repeatedly approaches and lingers, `D_comfort`
  shrinks (he tolerates closeness); if he flinches/retreats, it grows and the
  barrier pushes the robot back further. The controller *earns* proximity from
  the dog's own behavior — it never assumes it.

This couples **estimation → safe control** in a closed loop, which is the pair
of skills a robotics reviewer is looking for.

---

## 6. Composition with existing fail-safes (must degrade safe)

The barrier layer sits **above** the existing stack and must never weaken it:

- **Sensor dropout (ToF lost / dog out of frame):** `d_rd` unknown ⇒ barrier
  cannot be evaluated ⇒ **fall back to the existing exposure gate**, i.e. treat
  as if below exposure 2 → no distance-reducing motion permitted. Missing data
  is treated as "at the boundary," never as "safe to advance."
- **Command-link watchdog** *[existing, ~1 s]* — unchanged and still supreme:
  if the Pi (estimator + QP) goes silent, the Arduino executor stops the robot
  regardless of the barrier. The CBF never overrides the dumb-executor stop.
- **Exposure gate** *[existing]* — still the outer authority. The barrier can
  only ever be *more* conservative than the exposure level already allows.
- **Power-cycle → exposure 0** *[existing]* — unchanged; a reboot still returns
  to no-motion.

Invariant to state and test: **no single sensor or compute failure can produce
a distance-reducing command.** (Now `docs/SAFETY.md` **Layer 3 — Distance
barrier**, with the bench test: spoof `d_rd` below `D_comfort` and confirm the
served `MOVE` forward component clamps to ≤ 0; pull the ToF and confirm fallback
to exposure-gate behavior. The Pi-side implementation is `optimized/control/`,
whose `--selftest` asserts this invariant three independent ways.)

---

## 7. Implementation mapping (fits the existing 3-tier split)

| Tier | Device | Added responsibility |
|---|---|---|
| Real-time | Arduino | unchanged — dumb executor, motor PWM via L298N, ~1 s motion timeout |
| Coordinating | **ESP32** | **read 2× quadrature encoders (PCNT)**; stream encoder+IMU+ToF at ≥10 Hz; existing sensor/arousal duties |
| Host | Pi 5 | **EKF state estimate + comfort-margin update + CBF-QP**; existing vision/dashboard |

Rationale for encoders → ESP32: the Uno's two hardware interrupts get tight
with two quadrature channels each; the ESP32's PCNT peripheral counts in
hardware with zero CPU cost. This keeps the Arduino a pure, certifiable executor.

**Hardware delta from current build:** 2× encoder gearmotors + matching wheels
(replace both bare DC motors); everything else already in hand (Pi 5, VL53LX,
IMU, L298N, ESP32, Arduino, dispenser, buzzer, Meccano chassis + printed
castor). Motors mount to the Meccano grid via their brackets on short adapter
strips; both mounted mirror-image on one axle line for a symmetric differential
drive (clean kinematics).

---

## 8. Validation (extends `study/STUDY_PROTOCOL.md`)

Pre-register these *before* running — they test the *controller*, distinct from
the behavioral RQs already in the protocol:

- **Safety (primary):** count of barrier violations (`h < 0` events) over all
  sessions. Target: **zero**. This is the headline claim — the certificate held
  on real hardware with a live, unpredictable subject.
- **Odometry integrity:** closed-loop pose error vs. tape-measured ground truth
  over a fixed drive (report drift %; justifies the state estimate the barrier
  trusts).
- **Comfort adaptation:** trajectory of `D_comfort(s)` over the study — does the
  margin the robot maintains shrink as Shifu habituates? (Ties the control
  layer to the behavioral outcome.)
- **Ablation:** fixed-margin barrier vs. adaptive-margin barrier — does
  adaptation change min-distance-maintained and approach frequency?
- **Welfare precedence** *[existing rule]* still outranks all of the above.

---

## 9. Open decisions (do NOT silently resolve)

1. **Identity sensor: ArUco vs. learned detector. — RESOLVED: YOLO.** The
   identity/bearing sensor is a learned **YOLO** dog detector
   (`vision/detect_yolo.py`, COCO `dog` class, exported to NCNN for the headless
   Pi 5); ArUco (`detect.py`) is retained only as a backward-compatible fallback
   reporter, and on the ROBOT in fixed-room camera mode C
   (`CANIS_ROBOT_MARKER_ID`) to localize the rigid robot — never on the dog. This
   removes the collar-marker fragility (orientation loss on a head-turn, a tiny
   tag on a moving 16 kg dog) that `[OPT-12]`'s grace period was band-aiding.
   **Crucially, this choice does NOT touch the safety certificate:** the barrier
   keys off the **VL53LX ToF distance**, and the detector only *gates* which ToF
   return is "the dog" (bearing selects dog-vs-wall). Identity and safety are
   therefore on independent sensors — a perception miss degrades to "dog unknown"
   (→ `d_rd` at the boundary → no distance-reducing motion, §6), it can never
   forge a distance. All three camera architectures (A onboard / B esp32cam
   stream / C fixed-room), selected by `CANIS_CAMERA_MODE`, POST the identical
   detection contract, so this resolution holds regardless of camera placement.
2. **`α`/`γ` and `D_min/D_max`** — start conservative (large `D_min`, small
   `γ`), tune upward only against logged data, never toward the dog.
3. **Estimator depth** — complementary-filter v1 vs. EKF. Ship the simpler one
   first; upgrade only if odometry error fails the §8 target.

---

## 10. Honest scope caveats

- This is a **single-subject** system (n = 1, Shifu). The certificate is a
  property of the *controller*, not evidence the intervention helps dogs
  generally. Keep the paper's existing framing.
- A CBF certificate assumes the model and the state estimate are trustworthy;
  its guarantee is only as strong as `d_rd`. That is *why* §8 makes odometry
  error and a zero-violation count first-class, measured results rather than
  assertions.
- No part of this reduces the priority of the welfare rule or the watchdog.
