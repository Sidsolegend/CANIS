# CANIS — Hardware bring-up test guide

Bench tests for each subsystem, ordered to build confidence (isolated first,
then combined). Pins: see [`WIRING.md`](WIRING.md). Each test = goal, steps,
pass bar, and the "don't panic" non-failures.

**Day-1 plan:** Tests 1, 2, 3 (all standalone, no WiFi/Pi). Then 4. Vision (5),
power (6), integration (7) after.

---

## Test 1 — Motors (Phase 1) — START HERE
**Goal:** L298N + motor firmware drive correctly. Arduino only.

**Wire:** Arduino 3→ENA, 4→IN1, 7→IN2, 8→IN3, 12→IN4, 5→ENB · motors→OUT1/2 &
OUT3/4 · 6 V pack **+**→+12V, **−**→GND · **L298N GND ↔ Arduino GND** · 5 V
jumper **ON**.

**Do:** Verify(✓)+upload `Treat_Dispenser_CANIS.ino` → Serial Monitor **@ 9600**
→ **wheels off the ground** → type `FORWARD` `BACKWARD` `LEFT` `RIGHT` `STOP`.

**Pass:** FORWARD = both fwd; LEFT/RIGHT spin in place opposite ways; STOP halts.
Then moves on the floor.

**Don't panic:**
- Each command drives **~1 s then auto-stops** — that's the **watchdog**, not a
  bug. Re-type to keep going; `STOP` halts instantly.
- Wheel spins backward → swap that motor's two OUT wires.
- Strains but won't move → raise `DRIVE_SPEED`/`PIVOT_SPEED` (at 200/220) toward 255.

---

## Test 2 — Dispenser (Phase 2 re-check)
**Goal:** bottle + servo flap drops one treat per command (edit added a
`stopMotors()` at dispense start).

**Wire:** servo→9, buzzer→6, button→2 (existing rig + medicine bottle).

**Do:** same sketch/monitor. Press the **button**, and separately type `DISPENSE`
↵. Run the **20-trigger reliability test**.

**Pass:** ≥18/20 single drops, no doubles, no jams. Tune `GATE_OPEN_MS`/throat if not.

---

## Test 3 — Sensors (Phase 4) — NEW WIRING
**Goal:** 3 ultrasonics + PIR give clean confirmed reads. ESP32 only.

**Wire:** PIR→27 · TRIG/ECHO L=32/34, C=26/25, R=14/35 · **each ECHO through its
own 1 kΩ series** · sensor VCC→VIN(5 V), GND→GND · PIR jumper "H".

**Do:** Verify+upload `Sensors_Test.ino` → Serial Monitor **@ 115200**. Wait ~60 s
(PIR warm-up). Wave at each sensor; walk for the PIR.

**Pass:** each distance column tracks your hand; crossing 30 cm flips that zone to
`Y` after ~3 reads; PIR flips `Y` on motion. L/C/R columns confirm the *right*
sensor responds (catches swapped wiring).

**Don't panic:** SR04M-2 reads garbage <~25 cm (blind zone) — test it 30–100 cm.

---

## Test 4 — ESP32↔Arduino link (Phase 3)
**Goal:** new command words parse across the real link. Both boards.

**Wire:** ESP32 GPIO13 → Arduino pin 10 · **common GND**.

**Do:** both flashed → **ESP32** Serial Monitor (@115200): press `d`, then `f`, then `s`.

**Pass:** `d` → dispenser fires; `f` → wheels drive ~1 s; `s` → stop.

---

## Test 5 — Vision (Phase 6) — Pi
Follow [`../vision/SETUP.md`](../vision/SETUP.md), which escalates:
1. `rpicam-still -o /tmp/cam.jpg` — camera/cable alive.
2. `python3 detect.py --snap` — pipeline sees marker ID 0.
3. `python3 detect.py --test --side left` (dashboard up) — ESP32 prints
   `[VISION] dog in zone: kitchen-left`, arousal climbs.
4. `python3 detect.py` — live: marker in frame trips it.

**Pass:** step 4 — marker reliably posts the zone and the ESP32 reacts.

---

## Test 6 — Power / buck (Phase 8) — DO CAREFULLY
**Goal:** clean power for untethered running. Use a multimeter.
1. Fresh 6 V pack reads ~6.0–6.5 V.
2. L298N fed + 5 V jumper on → its **+5V terminal ≈ 5 V**.
3. **Set the buck output to 5.0–5.1 V BEFORE connecting the Pi** — feed it from
   the pack, turn its pot while metering the output. **Only then** connect the Pi.

**Pass:** Pi boots & stays up off the buck under load; motors run without
browning out the logic.

**⚠️ Critical:** never connect the buck to the Pi before dialing it to ~5 V — an
unset buck can sit at 10 V+ and **kill the Pi**.

---

## Test 7 — Full integration (Phase 9) — LAST
**Goal:** the whole loop, live.

**Prereq:** `arduino_secrets.h` (WiFi + Pi IP) in `CANIS_Core/`; dashboard running
on the Pi; same 2.4 GHz WiFi; **all grounds common**.

**Do:** Verify+upload `CANIS_Core.ino` (@115200).
1. **Manual:** `t` ×N → arousal climbs → AROUSAL → CORRECTION (800 Hz cue); `r` →
   CALM → **reward dispense**. Dashboard flips to "live — ESP32".
2. **Real sensors:** walk into zone → CORRECTION; leave → CALM → reward.
   (Rewards: 20 s cooldown, 30/session cap.)
3. **Motion (last, gentle):** wheels **off the ground**, press `m` to raise
   `EXPOSURE_LEVEL` to 1 → CORRECTION pulses an orient toward the active sensor;
   leaving CORRECTION → instant STOP.

**Pass:** detect → arousal → CORRECTION cue + orient → leave → reward, all on the
dashboard. **That's the print greenlight** (see the print gate before any print run).
