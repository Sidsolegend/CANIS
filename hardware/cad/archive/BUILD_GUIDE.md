# CANIS - Build Guide

Two-tier robot, ~205 × 165 mm footprint. Print the 9 parts, bolt it together, wire
it per [`../../WIRING.md`](../../WIRING.md). All printed holes are sized for **M3
self-tappers** (they thread straight into the printed pilot holes - no nuts/inserts
needed) unless noted.

---

## 1. Parts you print (`hardware/cad/stl/`)

| File | Qty | What it is |
|------|-----|------------|
| `deck.stl` | 1 | Lower deck - carries Pi, L298N, buck, motors, castor, dispenser |
| `shelf.stl` | 1 | Upper shelf - carries Arduino, breadboard, ESP32 |
| `shell.stl` | 1 | Outer dome + paw face |
| `dispenser.stl` | 1 | Rotating-disc housing + funnel |
| `disc.stl` | 1 | The metering disc (sits on the servo horn) |
| `chute.stl` | 1 | Treat chute (bolts under the deck) |
| `lid.stl` | 1 | Hopper cap |
| `castor.stl` | 1 | 9 mm leveling spacer for the castor |
| `sensor_plate.stl` | 1 | Holds camera + ultrasonic + PIR behind the paw |

(`canis_all.stl` is all 9 on one plate - ignore it if you're printing the
individual files.)

---

## 2. Print settings - PLA

**Global:** 0.4 mm nozzle · 0.2 mm layers · ~210 °C / 60 °C bed · no enclosure
needed. Add a **brim** for the tall/small parts (dispenser, disc, castor).

| Part | Orientation | Supports | Walls | Infill | Why |
|------|-------------|----------|-------|--------|-----|
| **deck** | flat, as-modelled | none | **4** | **30 %** | load-bearing |
| **shelf** | flat | none | 3 | 25 % | carries boards |
| **shell** | **open side DOWN** (dome up) | **tree**, only under the paw + hopper rim | 3 | 12–15 % | cosmetic; print in **matte PLA** to hide facets |
| **dispenser** | funnel mouth **UP** | none (PLA bridges) | 3 | 20 % | |
| **disc** | flat | none | **4** | **40 %** | small, spins - keep it stiff/round |
| **chute** | flange **down**, trough up | light, under the trough overhang | 3 | 20 % | |
| **lid** | flat | none | 3 | 20 % | |
| **castor** | flat | none | 4 | 40 % | takes the rear load |
| **sensor_plate** | flat | none | 3 | 20 % | |

For a cleaner shell surface drop it to **0.16 mm layers**. Everything fits a
256 mm bed (Bambu P1S etc.).

---

## 3. Hardware / electronics (bring your own)

- Raspberry Pi 5 (in the CanaKit case) · Arduino Uno · ESP32 DevKit V1
- L298N motor driver · LM2596 buck converter
- 2× yellow DC gearmotors + 68 mm wheels + their **metal brackets** (reused) · 1× ball castor (≈25 mm)
- **SG90** micro-servo (drives the disc)
- HC-SR04 ultrasonic · HC-SR501 PIR · a camera (Pi Cam module or USB webcam)
- 2× buzzer · push-button · 1 kΩ resistor · capacitors · half-size **breadboard** · jumper wires
- Power: 5 V USB now; battery + the buck later (see WIRING.md)
- **Fasteners:** ~20× **M3 × 8 mm** self-tapping screws · 4× **M2 × 6 mm** (camera) ·
  double-sided tape (breadboard) · the SG90's own horn screw

---

## 4. Assembly - step by step

> Do the **lower deck wiring before the shelf goes on** - the shelf covers it.

**A. Lower deck**
1. **Pi 5** - just **drops into the centre cradle** (snug fence, no screws). Ports face out the side gaps.
2. **Motors** - bolt your metal brackets to the 4 mid-side deck holes (`M3`), clip the gearmotors in, push the 68 mm wheels on.
3. **Castor** - screw the `castor` spacer to the rear of the deck (4× M3 from above), then screw your ball castor to the bottom of the spacer.
4. **L298N** and **buck** - screw each to its 4 standoffs (`M3`).

**B. Dispenser (rotating disc)** - see §6 for how it works
5. Drop the **SG90 upright into the `dispenser` housing**, shaft pointing up through the centre.
6. Press the **`disc` onto the servo horn** (centre socket), secure with the servo's horn screw. Check it spins freely in the chamber.
7. Screw the **`dispenser` to the deck** (3× M3 feet) at the rear.
8. From **under** the deck, screw the **`chute`** flange to the deck (2× M3) so its inlet lines up with the deck's treat-exit hole.

**C. Upper shelf**
9. Route the lower-deck cables upward, then set the **`shelf` on the 4 standoffs** and screw down (4× M3 from the top). The funnel passes up through the shelf notch.
10. **Arduino** and **ESP32** - screw to their standoffs (`M3`). **Breadboard** - peel-and-stick into the lip recess.
11. Wire the boards (ESP32↔Arduino serial, sensors, buzzers, button) per WIRING.md, using the shelf's wire holes + the deck's cable slots.

**D. Sensors + shell**
12. Mount the **camera** (4× M2), **HC-SR04** (cans through the two holes), and **PIR** (dome in the ring) onto the **`sensor_plate`**; wire them.
13. Screw the `sensor_plate` to the **2 bosses behind the paw** inside the shell (2× M3).
14. **Drop the `shell`** over the whole robot - the funnel pokes out the top opening, the wheels sit in the arches, the paw lines up front. Put the **`lid`** on the funnel.

Power up and test.

---

## 5. Wiring

Every pin, wire and the power architecture is in **[`../../WIRING.md`](../../WIRING.md)** - follow that for the ESP32 ↔ Arduino ↔ Pi connections, sensors, motors and power.

---

## 6. How the dispenser works (where the servo goes)

```
        funnel (kibble)
             │ inlet (one side)
   ═══════[ DISC ]═══════   ← 1 pocket; sits on the servo shaft
        ▣ SG90 (upright) ▣   ← servo stands INSIDE the housing, shaft UP into the disc
   ═════════════════════
             │ exit (other side) → through the deck → chute → floor (rear)
```
The pocket fills with one kibble under the funnel, the servo turns the disc **180°**,
and that kibble drops out the exit → down the chute → onto the floor at the rear.
SG90's 180° range = one dispense; turn back to reload.

---

## 7. Numbers to confirm for *your* hardware (`// CONFIRM` in `canis.scad`)

- **Camera** - `cam_d / cam_hx / cam_hy` are set for a Pi-cam module; change for your part, re-export `shell` + `sensor_plate`.
- **`castor_spacer_h` (9 mm)** = rear ride height − your 25 mm castor; trim once assembled so the deck sits level.
- **`post_h` (42 mm)** - shelf standoff height; must clear your Pi case + cables.
- **Servo** - the SG90 shaft is offset ~5.5 mm from its body centre; check it drops in with the shaft centred before gluing.
