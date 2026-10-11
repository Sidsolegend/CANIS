# CANIS - metal chassis prototype build

Only the **dispenser housing + flap** are 3D printed so far (per the print
gate: no full print run until this bench build proves out). Everything else - Pi 5, Arduino, ESP32, L298N, buck, motor pack, wheels, castor, sensors - mounts on the metal chassis you already have. This is a bench/rolling
prototype, not the final enclosure.

Layout below mirrors the coordinates already modeled in
`hardware/cad/canis_v4.scad` (the printed deck), so nothing here is
throwaway - migrating later is re-drilling the same hole pattern into the
printed deck instead of the metal plate.

---

## 1. Reference layout (top-down, origin = plate center)

```
                    REAR
   [4xAA pack]                    [dispenser]
      front                          rear
        \                              /
  ------------------------------------------
  |                                        |
  |     [ L298N ]      [ Pi 5 ]            |
  |                                        |
  |     [ buck  ]                          |
  |                                        |
  ------------------------------------------
   (L)=====wheel            wheel=====(R)
              castor (front, centered)
   [Pi power bank strapped underneath, belly-mounted]
                    FRONT
```

Approximate spacing (from `canis_v4.scad`, mm, roughly to scale - adjust to
whatever your metal plate's actual footprint is, these are relative
positions not hard requirements):

| Part | Footprint | Position (x=fore/aft, y=left/right) |
|---|---|---|
| Pi 5 | 92 x 63 | center, x ≈ +8 (slightly rear of plate center) |
| L298N | 44 x 44 | x ≈ −74 (front), y ≈ +54 (right) |
| LM2596 buck | 44 x 22 | x ≈ −74 (front), y ≈ −54 (left) |
| Dispenser (servo+flap+funnel) | ~58 dia | x ≈ −72 (front), on centerline |
| 4×AA motor pack | 58 x 31 | x ≈ +80 (rear), centerline |
| Pi power bank | 100 x 65 | strapped **underneath** the plate, belly-mount |
| Wheels | 68 dia | axle at x ≈ +22, track = plate width + 12 |
| Castor | - | x ≈ −(half-length)+18, centerline |

If your metal chassis is a different size/shape, keep the *relative*
arrangement - dispenser and motor pack at opposite ends, L298N+buck flanking
the Pi, wheels near the Pi's axle line, castor at the far end from the
wheels for balance.

---

## 2. Mounting

- **Pi 5, L298N, buck, 4xAA pack:** zip-tie, VHB tape, or drill+M3 standoffs
  into the metal - whatever's fastest for a bench rig. None of these need to
  be structural yet; they just need to not shift when the robot moves.
- **Dispenser housing (printed):** 3 M3 feet in a triangle, **25 mm radius**,
  at angles 90°/210°/330° around the housing center (see `disp_pins()` in
  `canis_v4.scad:162`). Drill 3 matching holes in the metal, or start with
  tape/zip-ties for the first bench test since servo torque is minimal.
- **Treat drop:** directly under the dispenser's gate hole, either drill a
  ~16 mm hole through the metal so treats fall to the floor, or hang the
  dispenser off the **front edge** of the chassis so the treat clears the
  lip - simplest option, skips needing a chute for now.
- **Wheels/castor:** whatever your metal chassis came with; keep wheels on
  one axle line and the castor at the opposite end for a stable 3-point
  stance (2 wheels + 1 castor).

---

## 3. Wiring - exactly per `WIRING.md`, no changes needed

Full pin map is already documented there; summary of what connects where on
the assembled robot:

- **ESP32** (USB-powered for now): PIR → GPIO27, 3× ultrasonic TRIG/ECHO →
  26/25, 32/34, 14/35 (each ECHO through its own 1 kΩ resistor), buzzer →
  GPIO33, GPIO13 → Arduino pin 10 (command link), shared GND.
- **Arduino Uno** (USB-powered for now): servo (dispenser flap) → pin 9,
  buzzer → pin 6, button → pin 2, SoftwareSerial RX ← ESP32 (pin 10), L298N
  control on 3/4/7/8/12/5.
- **L298N:** motor battery (6 V pack or bench 5 V supply) → `+12V` terminal,
  **never** the Arduino's 5 V rail. Shared GND across ESP32/Arduino/L298N.
- **Pi 5:** own official 5 V/5 A PSU (or the power bank once untethered),
  WiFi-only link to the ESP32 - no wired connection, so its ground does not
  need to join the robot's.

Bring-up order - follow `TESTING.md` as-is: Test 1 (motors) → Test 2
(dispenser) → Test 3 (sensors) → Test 4 (ESP32↔Arduino link) → Test 5
(vision/Pi) → Test 6 (power/buck, only once you're moving to battery).

---

## 4. What's deferred to the full print

Deck, shelf, shell, lid, sensor plate, castor spacer - all replaced by the
metal chassis + its stock hardware for this phase. When the metal+dispenser
prototype proves out (per the print gate - [[canis-print-gate]]), the only
migration work is transferring this same component layout onto the printed
deck, which already has matching mount holes for every part listed above.
