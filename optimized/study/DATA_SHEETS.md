# CANIS — Printable Per-Session Data Sheet

Print one copy of the sheet below per session (or photocopy a stack before
the baseline week). Fill it by hand, in the room or right after — memory
decays within hours, so the manual line (§5.2 of the protocol) should be
written **before leaving the room**, per `STUDY_PROTOCOL.md` §6 step 4.

Every field name on this sheet matches a field defined in
`STUDY_PROTOCOL.md` exactly — either a `session_notes.csv` column (§9.1) or
an automatic metric (§5.1) — so transcribing this sheet into the CSV, or
cross-checking it against `/export/sessions.csv`, is a direct copy with no
renaming or re-interpretation needed.

Sections marked **[AUTO]** are computed by the dashboard/event log and
listed here only as a reference/cross-check — do not hand-calculate them;
just glance at the dashboard or `analyze_sessions.py` output afterward and
confirm nothing looks obviously wrong (e.g. a session with zero zone
episodes and a fear score of 3 is worth a second look).

---

## Session header

```
Date: ______________________        Session ID: ___________________________
      (also the dashboard's auto session_id — copy it, don't retype it by hand
       if the dashboard is up; otherwise leave blank and fill from the export
       later)

Exposure level (confirm on dashboard before starting): [ ] 0   [ ] 1   [ ] 2

Planned duration (min): __________     Actual duration (min): __________
                                        (20-40 min target — STUDY_PROTOCOL.md §3)

Study phase (circle one):   A1-baseline   B@E0   B@E1   B@E2   A2-probe

Start time: __________          End time: __________
```

## Pre-session checklist (STUDY_PROTOCOL.md §6, step 1)

```
[ ] Hopper filled — treats loaded (count): __________        <- treats_loaded
[ ] Robot powered on
[ ] Dashboard open, shows "live data — ESP32" (not simulator)
[ ] Exposure level shown on dashboard MATCHES the level circled above
[ ] ESP32 rebooted at session start (counters/session cap reset fresh)
[ ] Owner in another room or seated passively — no luring, no commands,
    no eye games with the dog during the session (§6 step 2)
[ ] Context check — anything unusual today? (see Context flags below)
```

## During the session — fear ethogram log (STUDY_PROTOCOL.md §5.3)

Score the dog's response in the **10 seconds after each robot-salient
event** (a cue, a motion, a dispense). Add rows as needed; this session's
median and max feed directly into the `fear_median` / `fear_max` fields
below.

| # | Time | Event (cue / motion / dispense / other) | Fear score (0–4) | Notes |
|---|------|-------------------------------------------|:---:|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |
| 8 | | | | |
| 9 | | | | |
|10 | | | | |

*(attach a second sheet if a session generates more than 10 scoreable events)*

**Fear score reference (STUDY_PROTOCOL.md §5.3) — keep this visible while scoring:**

| Score | Definition |
|:---:|---|
| 0 | No orienting to the robot, or approaches it loosely (body soft) |
| 1 | Orients/looks; returns to prior activity within ~5 s |
| 2 | Moves away ≥ 1 m at a walk; ears back OR tail lowered; settles < 30 s |
| 3 | Startle/flinch, trot/run away, avoids that part of the room > 1 min, refuses a treat he would normally take |
| 4 | Panic signs: bolting, hiding, trembling, cowering, vocalizing in distress, refusing food > 5 min |

## Welfare stop criteria — check continuously (STUDY_PROTOCOL.md §8)

**Stop the session immediately (power down, calmly) if ANY of these occur.**
This box exists to be glanced at mid-session, not just filled in after.

```
[ ] Any score-4 event
[ ] Two score-3 events within this session
[ ] Dog refuses two consecutive offered/dispensed treats he normally takes
[ ] Any physical contact between robot and dog
[ ] Any equipment behavior not covered by SAFETY.md's expected modes
    (weird noises, stuck motor, smoke, hot smell)

IF STOPPED: next session no sooner than the next day, one exposure level
lower (min 0). Log it below.
```

## End-of-session checklist (STUDY_PROTOCOL.md §6, steps 3–4)

```
[ ] Counted remaining treats in hopper (ground truth for dispensed/jam counts)
[ ] Noted any treats left uneaten on the floor
[ ] This sheet filled out BEFORE leaving the room
```

---

## Session record (transcribe into `session_notes.csv` — columns match exactly)

```
date:              ______________________
session_id:        ______________________
exposure_level:    ______________________
planned_min:       ______________________
actual_min:        ______________________
treats_loaded:      ______________________
treats_remaining:  ______________________
treats_taken:      ______________________   (taken, not just dispensed —
                                              acceptance ratio is a fear index;
                                              STUDY_PROTOCOL.md §5.2)
fear_median:       ______________________   (median of the scores logged above)
fear_max:          ______________________   (max of the scores logged above)
welfare_stop(Y/N): ______________________
stop_reason:       ______________________________________________________
context_flags:     ______________________________________________________
                   (visitors earlier, weather/noise, unusual schedule, etc.)
notes:             ______________________________________________________
                   ______________________________________________________
                   ______________________________________________________
```

---

## [AUTO] Automatic metrics — cross-check against the dashboard, don't hand-compute

These come from the event log via `/export/sessions.csv` or
`analyze_sessions.py` (`STUDY_PROTOCOL.md` §5.1). Glance at them after the
session and jot down anything that looks off (e.g. zone episodes = 0 but a
fear score was logged — that's a data-quality flag, not a typo to silently
fix).

| Metric | Source | Sanity-check value (optional, fill after export) |
|---|---|---|
| Zone episodes (count) | `zone_seen` clusters | |
| Time-in-zone (s) | episode spans | |
| First-zone latency (s) | session start → first sighting | |
| Correction episodes | `state_transition` → CORRECTION | |
| Time in CALM / AROUSAL / CORRECTION (s) | transition integration | |
| Disengagement latency (s) — **primary RQ2 endpoint** | CORRECTION-entry → next CALM-entry | |
| Treats dispensed | `treat_dispensed` events | |
| Jams | `dispenser_jam` events | |
| Exposure level (as logged) | `exposure_change` + snapshot | |
| Link gaps / reboots | `link_gap`, `firmware_reboot` | |

**Anomaly flag:** if `treats dispensed` (auto) ≠ `treats_loaded −
treats_remaining` (hand-counted), note it in `notes` above — it usually
means a treat was eaten off the floor without a dispense, or a jam wasn't
cleared.

---

## Weekly rollup reminder (STUDY_PROTOCOL.md §6 step 4)

```
[ ] Downloaded /export/sessions.csv this week
[ ] Ran analyze_sessions.py and eyeballed the plots
[ ] Checked this level's advance/hold/retreat criteria (§2.1):
    - Median fear score ≤ 1 over last 5 sessions at this level?      Y / N
    - No session with a score-3+ event in that window?                Y / N
    - Treat acceptance ≥ 80% over that window?                        Y / N
    -> All three Y = eligible to advance one exposure level.
    -> Any retreat trigger hit (score-4; 2 consecutive sessions
       median fear ≥ 2; treat acceptance < 50% in a session)?          Y / N
    -> If Y: drop one exposure level for ≥ 10 sessions before retrying.
```
