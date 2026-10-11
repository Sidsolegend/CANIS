# CANIS - Build Guide (v2)

Source: **`canis_v2.scad`** · STLs: **`stl_v2/`** · renders: **`preview_v2/`**.
(v1 `canis.scad` + `stl/` are left intact for reference.)

**What changed vs v1**
- **Dispenser is now a FLAP GATE** (was a rotating disc). The SG90 swings a printed
  paddle ~80° off a gate hole - matches the tested firmware (`OPEN=0 / CLOSED=80`),
  **no firmware change**. `disc.stl` → **`flap.stl`**.
- **3 ultrasonics**: paw keeps the camera + PIR + the **SR04M-2 (single centre hole)**;
  the two **HC-SR04** mount on **angled openings on the head front-left/right** (~32° out)
  for left/centre/right bearing.
- **Power bays added**: a **4×AA cradle** on the deck `[5]` and an **under-deck strap
  mount for the Pi power bank** `[6]`.
- **Numbered labels [1–10]** engraved on the deck/shelf + wire-routing slots.

Two-tier robot, ~205 × 165 mm. M3 self-tappers thread into printed pilot holes.

---

## 1. Parts you print (`stl_v2/`)

| File | Qty | What it is |
|------|-----|------------|
| `deck.stl` | 1 | Lower deck - Pi, L298N, buck, motors, castor, dispenser, 4×AA, power-bank straps |
| `shelf.stl` | 1 | Upper shelf - Arduino, breadboard, ESP32 |
| `shell.stl` | 1 | Outer dome + paw + angled L/R ultrasonic openings |
| `dispenser.stl` | 1 | **Flap-gate** housing + funnel |
| `flap.stl` | 1 | The gate paddle (presses on the SG90 horn) |
| `chute.stl` | 1 | Treat chute (bolts under the deck) |
| `lid.stl` | 1 | Hopper cap |
| `castor.stl` | 1 | Rear leveling spacer |
| `sensor_plate.stl` | 1 | Camera + SR04M-2 + PIR carrier (behind the paw) |

---

## 2. Print settings - PLA

**Global:** 0.4 mm nozzle · 0.2 mm layers · ~210 °C / 60 °C bed · brim on the tall/small parts.

| Part | Orientation | Supports | Walls / Infill |
|------|-------------|----------|----------------|
| deck | flat, as-modelled | none | 4 / 30 % |
| shelf | flat | none | 3 / 25 % |
| shell | open side DOWN (dome up) | tree, under paw + hopper rim only | 3 / 12–15 % (matte PLA) |
| dispenser | funnel mouth **UP** | none (PLA bridges) | 3 / 20 % |
| flap | flat | none | 4 / 40 % (small, moving) |
| chute | flange down | light under the trough | 3 / 20 % |
| lid / castor / sensor_plate | flat | none | 3–4 / 20–40 % |

Everything fits a 256 mm bed.

---

## 3. Hardware (bring your own)

- Raspberry Pi 5 (case) · Arduino Uno · ESP32 DevKit V1
- L298N motor driver · LM2596 buck converter
- 2× yellow DC gearmotors + 68 mm wheels + metal brackets · 1× ball castor (~25 mm)
- **SG90** micro-servo (+ its horn + horn screw) - drives the flap
- **2× HC-SR04** (angled L/R) + **1× SR04M-2** waterproof (centre) ultrasonic
- HC-SR501 **PIR** · **Pi Camera (OV5647)** + CSI ribbon · 2× buzzer · button
- **3× 1 kΩ** resistors (one per ultrasonic ECHO) · capacitors · half-size **breadboard** · jumpers
- Power: **4×AA holder** (motors, via L298N) · **USB power bank ≥3 A** (Pi) · buck (logic)
- Fasteners: ~20× M3×8 self-tappers · 4× M2×6 (camera) · double-sided tape (breadboard)

---

## 4. Assembly (numbered to the engraved labels)

> Wire the lower deck before the shelf goes on.

**Lower deck**
1. **[1] Pi 5** - drops into the centre cradle (ports out the side gaps).
2. Motors - brackets to the 4 mid-side holes; wheels on.
3. Castor - `castor` spacer to the rear, ball castor under it.
4. **[2] L298N** and **[3] buck** - each to its 4 standoffs.
5. **[5] 4×AA pack** - slide into the front cradle (open end faces forward).
6. **[6] Pi power bank** - strap it **under** the deck through the 4 slots (velcro/zip tie).

**[4] Dispenser (flap gate)** - see §6
7. Drop the **SG90 upright** into the `dispenser` housing, shaft up.
8. Press the **`flap`** onto the servo horn (cross-socket); check it sweeps freely in the chamber.
9. Screw the `dispenser` to the deck (3× M3 feet); screw the **`chute`** under the deck at the exit.

**Upper shelf**
10. Route cables up through the slots; set the **`shelf`** on the 4 standoffs.
11. **[7] Arduino**, **[8] ESP32** to standoffs; **[9] breadboard** peel-and-stick.
12. Wire per `../../WIRING.md` (each ultrasonic ECHO through its own 1 kΩ).

**Sensors + shell**
13. On `sensor_plate`: **camera** (top, M2), **SR04M-2** transducer (centre ring), **PIR** (bottom ring).
14. Screw the plate to the 2 paw bosses inside the shell.
15. Mount the **2 HC-SR04** behind the **angled L/R openings** in the shell (glue/clip).
16. Drop the **`shell`** over everything - funnel out the top, wheels in the arches, paw forward. **`lid`** on the funnel.

---

## 5. How the dispenser works (flap gate)

```
        funnel (treats)
             │  one-treat throat
        ── [ FLAP ] ──   ← paddle on the SG90 horn, sits under the throat
        ▣ SG90 (upright) ▣
             │ gate hole (under the flap) → deck → chute → floor (rear)
```
The throat holds one treat on the flap. The servo swings the flap ~80° clear → the
treat drops through the gate → chute → onto the floor behind the robot. `GATE_OPEN_MS`
in the firmware is the main reliability knob; the narrow throat keeps it to one at a time.

---

## 6. Tunables in `canis_v2.scad` (`// CONFIRM`)

- **`treat_d` (9 mm)** - match your treats; resizes the throat + gate.
- **`aa_*` / `bank_*`** - battery + power-bank bay dimensions (set to your exact parts).
- **`us_yaw` (32°)** - outward aim of the L/R ultrasonics. If a printed eye-hole clips
  an edge, nudge this or `us_side_y`.
- **`cam_d/cam_hx/cam_hy`** - set for the OV5647; change for a different camera.
- **`post_h` (42 mm)** - shelf standoff height; must clear your Pi case + cables.

Number key: 1 Pi · 2 L298N · 3 buck · 4 dispenser · 5 4×AA · 6 power bank (under) ·
7 Arduino · 8 ESP32 · 9 breadboard · 10 sensors.
