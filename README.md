# CANIS

[](https://github.com/Sidsolegend/CANIS/actions/workflows/tests.yml)

**A welfare-first conditioning robot for a fearful dog.**
CANIS rewards my Goldendoodle, Shifu, for *leaving* forbidden zones — it never
punishes him for entering them. Raspberry Pi 5 vision (ArUco) + ESP32 arousal
state machine + Arduino real-time executor, wrapped in fail-safes designed
around one hard constraint: **Shifu is afraid of moving objects**, so robot
motion is a rationed hazard introduced over weeks, not a free actuator.

> Proposed drop-in replacement for the root `README.md`. Links assume it sits
> at the repo root; the `optimized/` tree holds the hardened sources and the
> full documentation set.

---

## Why this exists

Consistent, precisely-timed reinforcement is the thing owners are worst at and
machines are best at. But the obvious design — a robot that "corrects" by
approaching the dog — would be actively harmful to a movement-fearful animal.
CANIS inverts it:

1. **Reward leaving, never punish entering.** The salient events in the dog's
   world stay appetitive (a marker tone that predicts food), not threatening.
2. **Motion is gated, not assumed.** A single explicit `EXPOSURE_LEVEL`
   (0 = never move · 1 = pivot only, never approach · 2 = brief gentle
   approach) sits between "the robot decided" and "the robot moves". It boots
   at 0, rises one step at a time, and only on logged evidence that the dog is
   comfortable.
3. **The longitudinal record is the point.** The graded-exposure process
   produces a session-by-session fear-response dataset for one real dog —
   protocol in [`optimized/study/STUDY_PROTOCOL.md`](optimized/study/STUDY_PROTOCOL.md)
   (n = 1, honestly scoped: no efficacy claims).

## Architecture

```
 Pi 5 (eyes+face)          ESP32 (brain)                Arduino Uno (limbs)
 ArUco vision ─┐      ┌─ PIR + 3× ultrasonic       ┌─ motion watchdog (1 s)
 Flask dash ───┼─WiFi─┤  arousal integrator   UART ┤  L298N + motors
 event log ────┘      └─ CALM/AROUSAL/CORRECTION ──┘  servo gate + marker
                          EXPOSURE gate               beam-break jam sensor
```

Three tiers, matched to strengths and isolated as failure domains: the Uno is
a deliberately dumb real-time executor whose **motion watchdog stops the motors
within ~1 s of command silence** — so a crashed brain, a loose wire, or a WiFi
storm all converge on a *stopped* robot. The ESP32 runs a rate-driven arousal
integrator (+30/s during confirmed detection, −12/s decay) through a
hysteresis state machine; dropping back to CALM is what earns the treat. The
Pi does what MCUs can't: ArUco marker vision, the live dashboard, and the
append-only study log.

Full diagrams (system, state machine, dispense sequence, data flow):
[`optimized/docs/ARCHITECTURE.md`](optimized/docs/ARCHITECTURE.md).

## Safety mechanisms

| Mechanism | Trigger | Response |
|---|---|---|
| Motion watchdog (Arduino) | 1 s without a fresh command | motors stop — needs nothing else alive |
| Exposure gate | every correction cycle | no/limited motion; **boots at level 0** |
| Never-approach invariant | `FORWARD` bearing below level 2 | rewritten to an in-place pivot |
| Immediate STOP on disengage | leaving CORRECTION | halt now, don't wait for the watchdog |
| Watch-only AROUSAL state | arousal 20–70 | observe, never act |
| Confirmation filter + warm-up | raw sensor blips / PIR warm-up | 3-consecutive-read hysteresis; boot mask |
| dt clamp + vision-latch TTL | loop stalls / dead Pi mid-detection | no phantom CORRECTION, ever |
| Reward cooldown + session cap | < 20 s gap or > 30/session | treat held (over-feeding & hopper safety) |
| Jam detection + fault | beam-break sees no drop (×2) | dispenser goes **silent** — no marker without a treat |
| Dashboard auth + validation | unauthenticated LAN request | 401; all inputs bounded |

Every row has a written test procedure: [`optimized/docs/SAFETY.md`](optimized/docs/SAFETY.md).

## Repository map

```
CANIS_Core/              ESP32 integrated brain (original)
Treat_Dispenser_CANIS/   Arduino executor: dispenser + motors (original)
State_Machine_*/         reference sketches (offline / WiFi state machine)
Sensors_Test/  WiFi_*/   bring-up & diagnostic sketches
dashboard/               Flask dashboard (original)
vision/                  ArUco detection + Pi camera setup (original)
hardware/                WIRING.md · TESTING.md · OpenSCAD CAD + STLs
paper/                   systems-paper draft (honest n=1 framing)
optimized/               ★ hardened duplicates + full docs — start here
├── canis_config.h / dashboard/canis_config.py   single-source tunables (+ parity test)
├── CANIS_Core/ · Treat_Dispenser_CANIS/ · vision/ · dashboard/   [OPT-1..14]
├── tests/               51 pure-logic tests (pytest, no hardware needed)
├── study/               STUDY_PROTOCOL.md · analyze_sessions.py
├── docs/                ARCHITECTURE.md · SAFETY.md
├── reviews/             engineering/admissions reviews · roadmap · risks
└── OPTIMIZATIONS.md     every change, file:line-referenced
```

**What `optimized/` is:** after the original build worked end-to-end, I went
back through every file myself and ran a deliberate review-and-hardening pass
— with AI tooling assisting the search-and-fix loop — looking for exactly the
kind of bugs that hide in state machines and safety-critical code (the stale
vision latch, the level-1 forward-drive bug, config values that had drifted
between the `.ino` files and the dashboard). Every change is file:line-referenced
in [`optimized/OPTIMIZATIONS.md`](optimized/OPTIMIZATIONS.md), and the findings
that motivated them are in
[`optimized/reviews/ENGINEERING_REVIEW.md`](optimized/reviews/ENGINEERING_REVIEW.md).
I decided what to fix and why; the tooling sped up the search, not the
judgment. `optimized/` is that hardened, fully-documented result — the
original folders stay as-built and unmodified so the before/after is honest
and checkable.

## Quick start

**Dashboard (Pi):**
```bash
cd optimized/dashboard
pip3 install -r requirements.txt
export CANIS_TOKEN=$(openssl rand -hex 16)   # shared secret; see the .service file
python3 app.py                                # dev; waitress unit for real use
```
Open `http://<pi-ip>:5000`. With no ESP32 it shows a clearly-labeled simulator.

**Firmware (ESP32 + Uno):** copy `arduino_secrets.h.example` into
`optimized/CANIS_Core/` as `arduino_secrets.h` (gitignored) and fill in WiFi,
Pi IP, and the same token. Flash `optimized/CANIS_Core` (board: *esp32 by
Espressif*, monitor 115200) and `optimized/Treat_Dispenser_CANIS` (9600).
Bring-up order & pass criteria: [`hardware/TESTING.md`](hardware/TESTING.md);
pinout & power: [`hardware/WIRING.md`](hardware/WIRING.md).

**Vision (Pi):** [`vision/SETUP.md`](vision/SETUP.md), then
`python3 optimized/vision/detect.py --zone kitchen`.

**Tests (any machine, no hardware):**
```bash
python3 -m pytest optimized/tests/    # 51 tests: state machine, safety
                                      # invariants, config parity, event log
```

**Study tooling:**
```bash
python3 optimized/study/analyze_sessions.py --generate-example ex.jsonl   # SYNTHETIC demo
# real data: download /export/sessions.csv from the dashboard
```

## Status (as of 2026-07-03)

All subsystems hardware-tested individually (drive, dispenser, sensors,
end-to-end vision, live dashboard); full-loop integration with the printed
shell and the final power rail is the remaining build work — tracked in
[`optimized/reviews/ROADMAP.md`](optimized/reviews/ROADMAP.md). The behavioral study has
**not** started; its pre-registered protocol is written and the logging
pipeline is tested.

## Honesty & ethics

One dog, one home, owner-run. The paper draft
([`paper/canis_paper_draft.md`](paper/canis_paper_draft.md)) claims a
*system design*, not a behavioral outcome; the study protocol pre-declares its
limitations and hard welfare stop-criteria (any distress ends the session, and
repeated distress lowers the exposure level — the dog outranks the data).

## License

MIT — see [`LICENSE`](LICENSE).
