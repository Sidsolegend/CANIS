# CANIS: safety and welfare risk assessment

Supporting evidence for CREST Gold criterion **4.2** (decisions taking
account of ethical and safety issues).

This is a formalisation, not a new analysis: `optimized/PROBLEMS.md`
already carries a severity-ranked inventory including a welfare section
(W1 to W7), and paper §8 carries the ethics statement. This document
restates those in a standard likelihood × severity register and adds the
workshop and electrical hazards that a build-safety assessment requires.

Scoring: **Likelihood** and **Severity** each 1 (low) to 5 (high); **Risk**
= L × S. Residual risk is the score after the stated control is in place.

**Ordering note:** animal-welfare hazards are listed first, and that
ordering is deliberate. The project's first design constraint (C1, paper
§3.1) is *do no harm to a fearful animal*, and it outranks capability
everywhere in the system.

---

## 1. Animal welfare

| # | Hazard | Who | L | S | Risk | Control | Residual |
|---|---|---|---|---|---|---|---|
| W1 | **Robot motion frightens a fear-reactive dog.** The subject is measurably afraid of moving objects; a roaming robot is close to the worst case | Dog | 4 | 4 | **16** | `EXPOSURE_LEVEL` defaults to **0, no movement at all**, and the system is fully functional at Level 0 (buzzer + reward only). Levels advance one step at a time, only on logged evidence of comfort, and are reversible. `NEVER_APPROACH` means the robot can't close distance on the dog in any mode. Escalation requires a two-key arm-then-confirm, not one keypress. Exposure resets to 0 on boot | 4 |
| W2 | **Aversive conditioning by design.** An automated "corrector" pairing a zone with a frightening machine | Dog | 3 | 4 | **12** | The conditioning primitive is inverted: CANIS **marks and rewards leaving**, and never punishes entering. This is a design decision (C2), not a setting; there's no punishment path in the state machine to misconfigure | 2 |
| W3 | **Buzzer cue is itself aversive** | Dog | 3 | 2 | 6 | Mild 800 Hz / 150 ms tone, restricted to GUARD mode only. Redesign the cue on any pattern of fear response | 3 |
| W4 | **Marker tone with no treat behind it.** A jam or empty hopper erodes the conditioning by teaching the dog the marker is a lie | Dog | 3 | 3 | 9 | Rewards counted on the Arduino's confirmed delivery ACK, not on the ESP32's request; correlation id per dispense so a manual button press can't be miscredited; after two jams the dispenser goes silent rather than keep beeping over an empty hopper. **Partial:** the beam-break jam detector has no CAD mount and ships disabled, so verification is gate-cycle-only | 6 |
| W5 | **Over-feeding** | Dog | 3 | 3 | 9 | 20 s cooldown, 30-per-session cap, enforced in all modes with a compile-time guard. Treats used are a vet-recommended training treat; the per-session count varies with session length rather than being fixed, and the daily total is confirmed to fit comfortably inside a safe allowance | 2 |
| W6 | **Physical contact with the dog** | Dog | 2 | 4 | 8 | `NEVER_APPROACH` in every mode; correction motions duration-bounded and halted immediately on disengagement; sessions supervised | 2 |
| W7 | **Over-arousal rather than fear.** Chasing a fleeing object winds some dogs up | Dog | 3 | 2 | 6 | **Confirmed protocol: the session ends immediately on any sign of over-arousal**, watched for alongside (not instead of) the distress signs in §6. This is a supervision-enforced rule, not an automated one; the robot itself has no over-arousal sensing | 3 |
| W8 | **Reward latency conditions the wrong behaviour.** A ~5 to 9 s delay between the act and the treat may reinforce whatever the dog did in between | Dog | 3 | 3 | 9 | Recognised and recorded as a validity risk in `PROBLEMS.md` §H. The marker tone fires immediately and bridges the gap, which is the standard mitigation; latency reduction is open work | 6 |

## 2. Physical and electrical

| # | Hazard | Who | L | S | Risk | Control | Residual |
|---|---|---|---|---|---|---|---|
| P1 | **Uncontrolled motion from an electrical or software fault** (brownout, motor-noise glitch, crashed controller) | Dog, household | 3 | 5 | **15** | Hardware motion watchdog on the real-time Arduino halts motors within ~1 s of command-link silence, so a crashed ESP32, a dead network, or a pulled cable all fail to *stopped*, not to *moving*. Watchdog stops motors on any reset; exposure resets to 0 on boot; noise-suppression capacitors specified. **Run the brownout test before any untethered session** | 4 |
| P2 | **Blind reversing / wall-lock.** PLAY flee backs up with no rear sensor and can corner-lock | Furniture, robot | 3 | 2 | 6 | **Open.** Accepted with supervision; a rear ultrasonic is the fix | 6 |
| P3 | **Falls down stairs.** No cliff sensor | Robot, anyone below | 2 | 4 | 8 | **By design, single-floor only.** Deliberate omission, documented. Keep the robot on one floor; it's unsafe off it | 4 |
| P4 | **Buck converter mis-set.** More than 10 V into the logic rail or the Pi destroys boards | Equipment | 3 | 3 | 9 | Standing safety rule: meter the buck output to 5.0 to 5.1 V *before* connecting anything to it | 2 |
| P5 | **Servo stall spike browns out the Arduino mid-dispense** (SG90 and buzzer share the Uno's 5 V rail) | Equipment, conditioning integrity | 3 | 2 | 6 | Motors stopped before dispensing (convention-guarded); feed the servo from the buck if resets appear | 4 |
| P6 | **3.3 V ESP32 RX exposed to 5 V Arduino TX** | Equipment | 3 | 3 | 9 | 1 kΩ/2 kΩ divider (or level shifter) on the link; 1 kΩ series resistor on ultrasonic ECHO lines | 3 |
| P7 | **Marginal 6 V motor pack through the L298N** (~2 V drop): stall or weak drive | Robot | 3 | 2 | 6 | Recognised; uprate to a 9 V pack if drive is weak. A stalled motor near a fearful dog is a welfare issue as well as a mechanical one | 4 |

## 3. Workshop

| # | Hazard | Who | L | S | Risk | Control | Residual |
|---|---|---|---|---|---|---|---|
| B1 | **Soldering iron burns** | Builder | 3 | 3 | 9 | Iron in a stand, never put down loose; bench clear; switched off and confirmed cooling before leaving | 3 |
| B2 | **Solder fume inhalation** | Builder | 3 | 2 | 6 | Ventilated room, head out of the plume, short sessions | 2 |
| B3 | **3D printer hot end / heated bed burns** (Bambu P1S) | Builder | 2 | 3 | 6 | Enclosed printer; no reaching into the build volume during or straight after a print | 2 |
| B4 | **3D printing emissions** (VOCs, ultrafine particles) | Builder, household | 2 | 2 | 4 | Lid closed, ventilated room, not sited in a sleeping area | 2 |
| B5 | **Knife injury during part clean-up**, including the hand-cut servo window in the sealed v4 dispenser | Builder | 3 | 3 | 9 | Part clamped rather than hand-held; cutting stroke directed away from the supporting hand. This cut exists *because* a CAD defect shipped (E1); the clean fix is the two-piece `dispenser_split.scad` reprint | 4 |
| B6 | **Sharp edges on the perforated metal chassis** | Builder | 3 | 2 | 6 | Cut edges deburred before assembly; the chassis is handled during every wiring change | 3 |

## 4. Data, privacy and security

| # | Hazard | Who | L | S | Risk | Control | Residual |
|---|---|---|---|---|---|---|---|
| D1 | **Camera in a home.** Imagery capture or retention | Household | 3 | 4 | **12** | The camera is used only for on-device fiducial detection: a **zone label is posted, not imagery**. Detect.py has a `--snap` flag (`vision/detect.py:144`) that can save a still frame, and the tracked systemd unit (`optimized/vision/canis-detect.service:50`) doesn't pass it, but that only says how the *tracked* launcher starts it, not how it's actually run day to day. **Confirm the `--snap` diagnostic has never written frames to disk in practice, or state the retention policy** | 4 |
| D2 | **LAN attacker steers the robot or poisons its data** | Dog, household | 2 | 4 | 8 | Authentication token required on all mutating routes, plus input validation and a sandbox. The token **must** be set for any non-bench run. Assumes home-LAN trust. Open: the token is passed as a query parameter and can land in access logs; move it to an `X-CANIS-Token` header | 4 |
| D3 | **Home network credentials leaked in a public repository** | Household | 3 | 3 | 9 | Credentials moved to an untracked `arduino_secrets.h`; the home SSID was found in tracked test sketches and scrubbed, including from the audit file that recorded it. A provenance convention was added so stale claims can't drift unnoticed | 2 |
| D4 | **Dashboard or Pi failure mid-session loses data** | Project | 3 | 1 | 3 | Append-per-write JSONL with a torn-line-tolerant reader; the ESP32 runs offline, so safety is unaffected by dashboard loss | 2 |

---

## 5. Highest residual risks

1. **W1, motion frightening the dog (residual 4).** Irreducible by
   engineering: the only real control is the gradual-exposure protocol run
   patiently, with the dog's logged response gating every advance.
   Everything else is a backstop.
2. **W4, W8 (residual 6 each), still open.** The disabled beam-break jam
   detector, and reward latency. W7 (over-arousal) is no longer in this
   category: I've confirmed an explicit stop-on-over-arousal protocol
   (§1), bringing its residual down to 3.
3. **P1, uncontrolled motion (residual 4).** The watchdog is the strongest
   single safety property in the system, but the brownout test hasn't
   been run yet. That test is a precondition for untethered operation.

## 6. Ethical position on scope

The project does **not** claim to change the dog's behaviour, and this
assessment doesn't assume it does. No institutional animal-ethics approval
was obtained, because no behavioural result is being reported. The work is
scoped as a systems and design contribution with engineering validation.
If it's ever reframed toward an efficacy claim, prior review from an
animal-research ethics body and/or a qualified veterinary behaviourist is
required first, and the gradual-exposure protocol's fear-response coding
scheme would need pre-registering rather than being defined after the
data is in.

The animal's owner (me) consents to participation and can end any session
at will. Sessions are supervised throughout.

> **Resolved:** no mentor was used for this project, confirmed. The
> stop-session criterion is confirmed as: the session ends immediately on
> any sign of over-arousal, watched for alongside distress. The treat
> budget is confirmed as fitting comfortably within a safe daily amount,
> using a vet-recommended treat, with the per-session count varying by
> session length rather than fixed.
>
> **Still open:** the `--snap` camera-retention question (§4, D1). Whether
> it's ever written frames to disk outside the one bring-up test depends
> on how the device has actually been run, not on what the tracked
> launcher says, so only I can close it.

## 7. Review

This assessment covers the system in its current state: bench-validated
subsystems, metal chassis, supervised sessions, exposure Level 0. It must
be reviewed before advancing the exposure level, before any untethered
run, and before any session that isn't directly supervised.
