# CANIS: project timeline and plan

Supporting evidence for CREST Gold criteria **1.4** (plan and rationale) and
**1.5** (time planned and organised well).

Dates are derived from primary artefacts: the git commit history, and the
dated results log in `hardware/TESTING.md` (2026-07-06).
`BUILD_STORY.md` already organises the work into six phases; this
document anchors those phases in time and states the plan they were
executing.

**Hours: 84, confirmed by me.** This matches the artefact-reconstructed
estimate below almost exactly.

---

## 1. Planned approach, and why

The project's three design constraints (paper §3.1) dictated the build
order, not convenience:

**C1, do no harm to a fearful animal, forced safety to be built before
capability.** The subject is measurably afraid of moving objects, which
makes robot motion a hazard to be rationed rather than a free actuator. The
consequence for planning is unusual: the *stop* path, the watchdog, and the
exposure gate had to exist and be tested before the robot was ever allowed
to move near the dog. A conventional build order, get it driving and add
safety later, was never available here.

**Subsystems validated in isolation before integration.**
`hardware/TESTING.md` specifies bench tests ordered to build confidence:
motors, dispenser, sensors, then the inter-board link, then vision, power,
and only finally the full integration loop. This ordering exists because a
failure in an integrated system with three processors and two link
protocols is expensive to localise, and because an untested subsystem near
a fearful animal is a welfare risk, not just a bug.

**Mechanical last, and deliberately deferred.** Chassis and enclosure work
followed the electronics for the same reason as any parametric design, but
here it went further: the printed deck was eventually abandoned entirely
(Phase 5) in favour of an available metal chassis. Deferring the mechanical
commitment is what made that pivot cheap.

The risk this ordering accepts is that the behavioural work, the
gradual-exposure protocol that runs on a weeks-to-months calendar, can't
start until the engineering is safe enough to run. That risk materialised,
and it's the project's main open item (§4).

---

## 2. Timeline

| Phase | Dates | Work | Primary evidence | Est. hrs |
|---|---|---|---|---|
| **1. Core firmware + conditioning logic** | Jun 26 | Arousal state machine; the stuck `visionActive` latch found and fixed with a local TTL; `dt` clamped so a stalled loop tick cannot launch arousal; fixed-buffer vision zone; rollover-safe back-off | commits *"Initial commit"*, *"Harden CANIS_Core"*, *"Reward tuning"* (all Jun 26); `BUILD_STORY.md` Phase 1 | ~14 |
| **2. Three-tier link + dashboard** | Jun 26 | Pi to ESP32 vision command channel; ArUco detection script; Flask dashboard with systemd auto-start; credential flow via `arduino_secrets.h` | commits Jun 26 (5); `vision/detect_yolo.py` | ~10 |
| **3. Hysteresis + the safety-critical bearing fix** | Jun 26 to Jul 1 | State chatter at threshold fixed with separate enter/exit thresholds; the FORWARD-bearing violation of `NEVER_APPROACH` found and rewritten as an in-place pivot; dispenser ACK/correlation-id rework so treats are counted on confirmed delivery | `BUILD_STORY.md` Phases 2 to 3; commit *"fix level-1 approach safety bug (optimized)"* (Jul 1) | ~12 |
| **4. CAD, four revisions** | Jun 29 to Jul 1 | Parametric chassis and shell; three revisions each carrying a real mechanical interference; the intersection-based fit-check method adopted; v2 flap-gate dispenser, v3 angular shell | 14 CAD commits Jun 29 to Jul 1; `BUILD_STORY.md` Phase 4; `hardware/cad/` | ~16 |
| **5. Bench validation** | Jul 1 to Jul 6 | L298N motor firmware, three-ultrasonic sensing, Pi-camera vision, per-subsystem bench tests executed and logged | commit Jul 1; **dated results log in `hardware/TESTING.md` (2026-07-06)** | ~8 |
| **6. Write-up + public release** | Jul 1 to Jul 7 | Systems paper draft, state tests, single-source config, secured dashboard, README restructure | commits Jul 1, Jul 7 | ~10 |
| **7. Metal-chassis pivot + safe-motion upgrade** | Jul 19 | Printed deck abandoned for a Meccano-style perforated metal chassis; mounting/wiring/assembly order re-derived; control-barrier-function safe-motion package; YOLO vision option; mobile-gated firmware; v4 STLs; SSID scrubbed from tracked files; provenance convention added so stale claims can't drift unnoticed | 13 commits Jul 19; `docs/BUILD_STORY.md` Phases 5 to 6 | ~14 |

**Span:** 2026-06-26 to 2026-07-19 (24 days). **Total: 84 hours, confirmed.**

---

## 3. Gantt

```
                 Jun 26   29    Jul 1     6   7            19
                   |      |      |        |   |            |
1 Core firmware  ####
2 Tier link+dash ####
3 Hysteresis+fix ################
4 CAD (4 revs)          ################
5 Bench validation              ############
6 Write-up + release            ##############
7 Chassis pivot + CBF                              ##############
```

The two-week gap between 7 Jul and 19 Jul is real and is shown as a gap
rather than smoothed over: the project paused after the public release and
resumed with the chassis pivot.

---

## 4. How the plan held up

**The safety-first ordering worked, and proved itself twice.** Building
the stop path before the drive path is what made the Phase 3 discovery
survivable. The bearing logic could legitimately compute FORWARD as the
direction to face the dog, and the Arduino has no concept of "face" versus
"drive at," meaning the single guarantee the whole project rested on could
be violated by ordinary, correct bearing arithmetic rather than by a bug
in some rare branch. That was caught because the safety property was
explicit and being checked, not because the robot did something visibly
wrong.

**CAD cost far more than planned.** Four revisions, each with an
interference that only appeared once printed, at a full print cycle per
attempt. The plan assumed one or two iterations. What resolved it wasn't a
better eye but a change of method; see `BUILD_STORY.md` Phase 4 and §11 of
the report.

**Phase 7 wasn't in the plan at all.** The chassis pivot re-derived a
mounting and wiring layout that assumed a printed deck, onto a bare metal
plate with holes every 10 mm. The castor's 24 mm bolt circle and the
dispenser's three feet at 25 mm radius, 120° apart, don't land on a square
grid cleanly, and the resolution was to bolt whichever foot lands on a
real hole and zip-tie the rest, recorded honestly rather than pretending
the grid and the CAD agree.

**The open item.** The gradual-exposure protocol hasn't been run to the
point of producing logged behavioural observations. `PROBLEMS.md` names
this itself, and names the cause: *"build perfectionism means it never
actually runs with the dog."* A project that identifies its own dominant
failure mode in writing and still hasn't resolved it should say so
plainly, which is what §5 of the report does.

**What I would plan differently.** Timebox the mechanical work and start
the exposure sessions in parallel from the earliest safe configuration.
Exposure Level 0 is buzzer-only with no motion at all. It was runnable
from Phase 1, needed nothing from the chassis, and would by now have weeks
of logged fear-response data behind it. The build was sequenced as though
the behavioural work depended on the finished robot, and at Level 0 it
never did.
