# CANIS Longitudinal Study Protocol (n = 1, single-subject design)

**Subject:** Shifu — young male Goldendoodle, privately owned by the author,
living in the author's home. Known relevant trait: **fear of moving objects**.
**Investigator:** the owner (sole). No other people, animals, or institutions
are involved; every step below is designed to be run by one person,
asynchronously, on the owner's own schedule.
**Status:** PRE-REGISTRATION DRAFT. Nothing below has been run. All numbers in
§9 are *criteria*, not results. No data exists yet except engineering-validation
logs. This document should be committed (and dated) **before** the baseline
week begins — that commit is the pre-registration.

---

## 1. What this study is, and is not

**Research questions (in order of priority):**

- **RQ1 (exposure/desensitization):** Can robot motion be introduced gradually
  (exposure levels 0→1→2) without producing a fear response in Shifu, as
  measured by a pre-defined fear ethogram and approach behavior?
- **RQ2 (conditioning):** Does the reward-on-leaving contingency change Shifu's
  zone behavior — shorter zone episodes, faster disengagement after the cue,
  fewer correction episodes per session — relative to his own baseline?

**This is a single-subject (n = 1) design.** It can demonstrate an effect *in
this dog* with replication built into the design itself (multiple baselines /
criterion steps), which is a legitimate and long-established methodology in
applied behavior analysis. It **cannot** support any generalization to other
dogs, and no such claim will be made. It is also **not** veterinary advice;
if Shifu's behavior includes anything medically concerning, a vet comes first.

**Welfare precedence rule:** RQ1 outranks RQ2, and Shifu's welfare outranks
both. Any conflict between "getting data" and "the dog is uncomfortable" is
resolved for the dog, immediately, every time (§8).

## 2. Design

Two single-subject designs are combined, one per research question:

### 2.1 RQ1 — Changing-criterion design (exposure level)
The classic design for *gradually introduced* interventions: the treatment
intensity (here `EXPOSURE_LEVEL`) is stepped only when behavior stabilizes at
the current criterion, and each stable plateau replicates the effect.

- **Phase E0:** robot present, powered, `EXPOSURE_LEVEL = 0` (buzzer-only).
- **Phase E1:** orient-only pivots during corrections.
- **Phase E2:** orient + brief gentle approach.

**Advance criterion (all three, over the last 5 sessions at the level):**
1. Median fear score (§5.3) ≤ 1 in the robot-motion coding windows;
2. no session containing a score-3+ event;
3. treat acceptance ≥ 80 % (a fearful dog refuses food — food refusal near the
   robot is itself a fear index).

**Hold criterion:** anything between advance and retreat — stay and collect
more sessions. There is no calendar pressure; "weeks/months" is the design.

**Retreat criterion (any one):** a score-4 event; two consecutive sessions with
median fear ≥ 2; treat acceptance < 50 % in a session. → Drop one level for at
least 10 sessions before re-attempting. Two retreats from the same level ⇒ that
level is this dog's ceiling; the study reports that honestly and stays below it.

### 2.2 RQ2 — A-B with maintenance probes (a deliberate compromise of ABA)
A full **ABA/withdrawal** design (baseline → treatment → *remove* treatment and
show the behavior returns) is the stronger causal design, and this protocol
pre-commits to a *limited* version of it:

- **A1 (baseline week, §4):** robot present and logging, but the contingency
  disabled — no cues, no treats. Measures natural zone behavior.
- **B (treatment):** full reward-on-leaving loop at the current exposure level.
- **A2 (brief withdrawal probes):** ONE probe session (contingency off, logging
  on) after every 10 treatment sessions — enough to see whether zone behavior
  drifts back, without extended removal of an intervention the dog may be
  benefiting from.

Rationale for not running a full multi-week withdrawal: (i) ethically, if the
contingency is working, a long withdrawal deliberately re-exposes the household
to the unwanted behavior (some of which — eating forbidden objects — is a
*safety* problem for the dog); (ii) practically, an owner-run study cannot
blind itself, so the marginal inferential gain is limited. This trade-off is
declared here, in advance, and counted as a limitation (§10).

## 3. Session definition & schedule

- **Session:** a continuous period with CANIS powered, the dashboard logging,
  and Shifu with normal access to the monitored room. Target **20–40 min**,
  ended early any time by the welfare rules (§8).
- **Cadence:** 1 session/day, ≥ 5 days/week, at approximately the same time of
  day (log deviations). Sessions are solitary owner activities — no visitors,
  no other pets in the room, phone on silent. Fully async by construction.
- **Session boundaries in data:** each dashboard start creates a `session_id`
  (event log [OPT-14]); one dashboard run per session. Reboot the ESP32 at
  session start so `MAX_SESSION_REWARDS` and counters reset with the session.
- **Treat budget:** the session cap (30) times treat size must stay within the
  vet-appropriate daily allowance; use part of the normal food ration as treats
  if needed. Confirm the budget against Shifu's diet **before** baseline week.
  *(Owner action item — a number this protocol cannot supply.)*

## 4. Baseline week (Phase A1) — run FIRST

7 days, robot **present and powered** in its final position (so "novel object
in the room" is burned off before treatment ever starts), contingency disabled:

- Firmware in **observation mode**: exposure 0 *and* rewards disabled for the
  week. Simplest honest method with current firmware: unplug/empty the treat
  hopper and disconnect the ESP32→Arduino data line — sensing, state machine,
  and logging all run; nothing actuates. (A `CANIS_OBSERVE_ONLY` compile flag
  is the cleaner future version — see ROADMAP.)
- Collect the same metrics as treatment sessions (§5). This is the yardstick
  every treatment claim is measured against.
- Also during this week: verify vision coverage (marker detection range across
  the room), sensor false-positive rate overnight (empty room — any
  `zone_seen`/detection events with no dog present are a calibration problem to
  fix **before** treatment), and camera low-light behavior (blur-guard skip
  rate; ENGINEERING_REVIEW §4.5).

## 5. Measures

### 5.1 Automatic (event log → `analyze_sessions.py` / `/export/sessions.csv`)
Per session, computed from the JSONL event log, zero manual effort:

| Metric | Source | RQ |
|---|---|---|
| Zone episodes (count) | `zone_seen` clusters (gap > 3 s) | RQ2 |
| Time-in-zone (s) | episode spans | RQ2 |
| First-zone latency (s) | session start → first sighting | RQ2 |
| Correction episodes | `state_transition` → CORRECTION | RQ2 |
| Time in CALM/AROUSAL/CORRECTION (s) | transition integration | RQ2 (arousal-proxy) |
| Disengagement latency (s) | CORRECTION-entry → next CALM-entry timestamps | RQ2 (primary) |
| Treats dispensed / jams | `treat_dispensed`, `dispenser_jam` | integrity |
| Exposure level | `exposure_change` + snapshot | RQ1 |
| Link gaps / reboots | `link_gap`, `firmware_reboot` | data quality |

**Primary RQ2 endpoint:** median disengagement latency per session.
**Secondary:** time-in-zone per session; correction episodes per session.
The "arousal-proxy stats" (state occupancy) are supportive only — arousal is a
*controller variable*, not a validated measure of the dog's internal state, and
this protocol never calls it one.

### 5.2 Manual, per session (~2 min, owner-recorded)
A fixed line appended to a `session_notes.csv` (template §9.1) immediately
after each session — memory decays within hours, so log before leaving the room:

- treats **taken** (vs dispensed — acceptance ratio is a fear index),
- fear score for the session (§5.3): median + worst event,
- context flags (visitors earlier, weather/noise, unusual schedule),
- welfare action taken, if any (§8).

### 5.3 Fear ethogram (0–4, adapted for owner scoring)
Score the dog's response **in the 10 s after each robot-salient event** (cue,
motion, dispense); the *session* records the median and the max.

| Score | Operational definition |
|---|---|
| 0 | No orienting to the robot, or approaches it loosely (body soft) |
| 1 | Orients/looks; returns to prior activity within ~5 s |
| 2 | Moves away ≥ 1 m at a walk; ears back OR tail lowered; settles < 30 s |
| 3 | Startle/flinch, trot/run away, avoids that part of the room > 1 min, refuses a treat he would normally take |
| 4 | Panic signs: bolting, hiding, trembling, cowering, vocalizing in distress, refusing food > 5 min |

Owner-scored and unblinded — declared as a limitation (§10). Mitigations: the
definitions above are behavioral and binary-ish on purpose; scores 3–4 are
hard to misread; and the *automatic* metrics (latency, time-in-zone, treat
acceptance from dispensed counts) cannot be wishful-thinking'd.

## 6. Procedure per session (checklist)

1. Fill hopper (count pieces in), power robot, start dashboard, confirm
   "live — ESP32" and correct exposure level on the page.
2. **Do not stage the dog.** No luring into or out of the zone, ever — the
   study measures spontaneous behavior. The owner stays in another room or sits
   passively out of the interaction (reading, laptop); no commands, no eye
   games with the dog during the session. This also procedurally neutralizes
   ENGINEERING_REVIEW §1.4 (sustained-calm treats in an *empty* room): sessions
   only run while Shifu actually has access to the room.
3. End at the planned time (or per §8). Count remaining treats (ground truth
   for the dispensed/jam counters). Note treats left uneaten on the floor.
4. Append the manual line (§5.2). Weekly: download `/export/sessions.csv`, run
   `analyze_sessions.py`, and eyeball the plots for the criterion checks (§2.1).

## 7. Threats to validity (declared before data collection)

- **Reward latency (~5–9 s; ENGINEERING_REVIEW §1.5).** The marker fires at
  dispense, ~5 s (ultrasonic path) to ~9 s (vision path) after actual
  disengagement — well beyond the 1–2 s ideal for marker conditioning. The
  contingency may therefore condition "being out of the zone and settled" more
  than the discrete act of leaving. Declared; the disengagement-latency metric
  is defined machine-side, so at least the *measurement* is unaffected. A
  faster-release firmware change is future work and would be a protocol
  amendment, not a mid-study tweak.
- **No blinding; owner is coder and caregiver.** Mitigated (not removed) by the
  ethogram's concrete definitions and by machine-logged primary endpoints.
- **Maturation/history confounds.** A young dog changes over months regardless.
  The changing-criterion design helps for RQ1 (behavior tracking each criterion
  step is the signature); the A2 probes help for RQ2; neither eliminates it.
- **Single zone/room, single dog.** No generalization claims, period.
- **Instrument drift.** Firmware/tunable changes mid-study break comparability:
  freeze `canis_config.h` values for the study's duration; any change = new
  study phase, logged and dated. The config-parity test and the event log's
  `exposure_change`/`firmware_reboot` records make silent drift detectable.
- **Sensor false positives** (curtains, sun artifacts) inflate zone metrics —
  measured during baseline (empty-room overnight check, §4) and re-checked
  monthly.
- **Owner-presence effects.** The owner in the next room is part of the
  environment; keep it constant (same rough position/activity each session).

## 8. Welfare rules (hard, non-negotiable)

**Stop the session immediately** (power the robot down, calmly) if any of:
- any score-4 event;
- two score-3 events within one session;
- Shifu refuses two consecutive offered/dispensed treats he normally takes;
- any physical contact between robot and dog;
- any equipment behavior not covered by SAFETY.md's expected modes (weird
  noises, stuck motor, smoke, hot smell — also power down for the *robot's* sake).

**After any stop:** next session no sooner than the next day, one exposure
level lower (min 0). Log the stop + reason in `session_notes.csv`.

**Standing rules:** the dog is never confined with the robot; the door stays
open so he can always leave. Sessions are supervised (owner within earshot,
checking the dashboard). The robot never operates unattended around the dog.
If a pattern of score-3+ responses persists at exposure 0 (buzzer alone
frightens him), the *auditory* cue is too aversive for this dog: pause the
study and reconsider the cue (softer/quieter marker) — that is a protocol
amendment, not a tuning knob.

**Ethics framing:** subject is the investigator's own companion animal;
methods are limited to a mild auditory cue, food rewards, and pre-gated,
duration-bounded motion, under the fail-safes enumerated in `docs/SAFETY.md`.
No institutional review exists for this owner-run study and **therefore no
formal behavioral-efficacy claim will be published from it** (consistent with
`paper/canis_paper_draft.md` §8); the outputs are (i) a welfare-gated exposure
record and (ii) within-dog descriptive statistics.

## 9. Data & artifacts

### 9.1 Files
- `events.jsonl` — machine log (dashboard, append-only; back it up weekly).
- `sessions.csv` — from `/export/sessions.csv` or `analyze_sessions.py --csv`.
- `session_notes.csv` — manual sheet, one row per session, columns:
  `date, session_id, exposure_level, planned_min, actual_min, treats_loaded,
  treats_remaining, treats_taken, fear_median, fear_max, welfare_stop(Y/N),
  stop_reason, context_flags, notes`
- This protocol file, unchanged during the study except dated amendments at the
  bottom.

### 9.2 Analysis (all descriptive/visual — standard for single-subject designs)
- Per-session time-series plots of the primary endpoints with phase boundaries
  (A1 | B@E0 | B@E1 | probes | …) drawn in.
- Criterion checks (§2.1) computed over 5-session windows.
- Report medians and IQRs, not means (sessions are short and skewed).
- **No inferential statistics** — n = 1 A-B data cannot honestly support them;
  the design's evidence is replication across criterion steps and probe
  reversals, shown visually. (If desired later: Tau-U or PND effect sizes for
  single-case designs — optional, and clearly labeled.)
- The `--generate-example` output is SYNTHETIC pipeline-test scaffolding and is
  never mixed with real logs (different filename, `synthetic` flag, session_id
  prefix).

## 10. Declared limitations (to be repeated verbatim in any write-up)
n = 1; owner-run; unblinded; no institutional ethics review (therefore no
efficacy claim); limited withdrawal (§2.2); reward latency ~5–9 s (§7);
single room; fear scores owner-coded on an unvalidated (though operationalized)
scale; maturation uncontrolled.

---
*Amendments (append-only, dated):* — none yet.
