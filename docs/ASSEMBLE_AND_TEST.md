# CANIS - Assembly & Test Plan (metal-chassis build)

> **Note:** parts of this document were written before the October 2026 rebuild. Since then the PIR motion sensor was removed (the camera covers presence), the front sensor became a JSN-SR04M-2 ultrasonic, and the drive moved to N20 encoder motors. [BUILD_STORY.md](BUILD_STORY.md) lists what changed, and [KNOWN_ISSUES.md](KNOWN_ISSUES.md) has the current state.

Everything to build CANIS on the metal chassis + the parts you already printed,
wire it, flash it, and test it - in order. Only the **Pi power bank** is
deferred (see the last section); everything else, including the motors, you can
finish and test today.

Legend:  **[DO]** a build step · **[TEST]** verify before moving on · yes pass criterion.

---

## 0. Parts & tools

**Printed (in hand):** dispenser housing, flap, chute, castor, sensor_plate, lid.
No deck/shelf/shell - the metal chassis replaces them.
**Electronics:** Pi 5 (+camera), ESP32 DevKit V1, Arduino Uno, L298N, LM2596 buck,
SG90 servo, PIR, 3× ultrasonic (2× HC-SR04 + 1× SR04M-2), buzzer, 2 gearmotors + wheels.
**Power:** motor pack, LM2596 buck, 4×AA holder (+2×AA spare). Pi bank = later.
**Consumables:** 3× 1 kΩ resistors (US echoes), 1 kΩ+2 kΩ (ACK level shift), 2× 0.1 µF
caps (motor noise), M3 hardware / zip-ties / VHB, hobby knife, multimeter, ArUco marker.

---

## 1. Dispenser - knife-mod + servo

1. **[DO]** Flip the housing over; find the ~12 mm shaft hole in the bottom centre.
2. **[DO]** Cut a **~25 × 15 mm rectangular window** around it through the ~2 mm floor - this opens the sealed servo pocket (24.5 × 14 mm inside).
3. **[DO]** **Trim the SG90's two mounting tabs** so the bare body slides in.
4. **[DO]** Slide the servo up into the pocket, **shaft UP**, wires out the window.
5. **[DO]** (After firmware, §4) power it and command **CLOSED** - press flap `TRIM`/`d`
   in the self-test, or just leave it; then **press the flap onto the horn while it sits
   at CLOSED (80°)** so the pad covers the gate hole. Confirm it swings free.
6. **[DO]** Mount the housing (3 feet, zip-tie/tape ok). Gate hole over a **drilled ~16 mm
   hole** in the chassis (or hang it off the front edge). Bolt the **chute** underneath.
7. yes **Dry-fit check:** at CLOSED the flap pad fully covers the gate hole (SG90 shaft is
   off-centre - nudge the servo / re-seat the flap a spline tooth if it doesn't cover).

---

## 2. Mount on the chassis (top-down layout)

```
         REAR
  [4xAA pack]        (centreline)
  [ L298N ][ Pi 5 ][ buck ]
  (L)==wheel   wheel==(R)   <- axle line
      [dispenser + chute]
      [sensor_plate on front edge]
         castor (front centre)
  [Pi power bank strapped UNDER]
         FRONT
```

- **[DO]** Fix Pi (centre), L298N (front-right), buck (front-left), 4×AA (rear),
  dispenser (front-centre), wheels (axle line), castor (front-centre). Zip-tie/VHB/M3.
- **[DO]** Mount **sensor_plate** on the **front edge**, vertical: camera hole ~40 mm up,
  SR04M-2 + PIR ~24 mm up, the two HC-SR04 angled ~32° outward L/R.
- **[DO]** Power bank goes **underneath** (belly strap) - mount later with the bank.

---

## 3. Wiring (exact pins)

### ESP32 (DevKit V1)
| GPIO | To | Note |
|---|---|---|
| 27 | PIR OUT | 3.3 V, direct |
| 26 / 25 | US **CENTRE** TRIG / ECHO | ECHO via **1 kΩ** series |
| 32 / 34 | US **LEFT** TRIG / ECHO | ECHO via **1 kΩ** |
| 14 / 35 | US **RIGHT** TRIG / ECHO | ECHO via **1 kΩ** |
| 33 | buzzer + | 800 Hz cue |
| 13 | → Arduino **pin 10** | command link (3.3→5 V ok) |
| 4 | ← Arduino **pin 11** | ACK line - **5→3.3 V, via 1 k/2 k divider or level shifter** |
| VIN / GND | 5 V / ground | from buck (below) |

### Arduino Uno
| Pin | To |
|---|---|
| 9 | servo signal |
| 6 | buzzer |
| 2 | button (INPUT_PULLUP) |
| 10 | ← ESP32 GPIO 13 |
| 11 | → ESP32 GPIO 4 (via level shift) |
| 3, 4, 7, 8, 12, 5 | L298N ENA, IN1, IN2, IN3, IN4, ENB |
| 5V / GND | from buck / common ground |

### L298N
- OUT1/2 → left motor, OUT3/4 → right motor (cut plugs, screw bare leads in).
- **+12V ← motor pack +** (NOT the Arduino 5 V). GND → common. **+5V jumper ON.**
- Solder **0.1 µF across each motor's two terminals** (noise).

### Power topology (all wired today)
- **Motor pack → L298N +12V AND → buck input** (one "BATT+" node).
- **Set buck to 5.0–5.1 V with a multimeter FIRST**, then its output → ESP32 VIN +
  Arduino 5V + all sensor VCC. Pull the ESP32/Arduino USB once the buck is confirmed.
- **All robot grounds common:** ESP32, Arduino, L298N, buck−, motor pack−.
- **Pi → its own wall PSU** now (WiFi-only, ground stays isolated). Bank later.
- Motor pack = 4×AA (6 V); if motors are weak/stall, **add the 2×AA → 6×AA (9 V)**.

---

## 4. Firmware

Before flashing, in `canis_config.h`:
- `CANIS_MODE_DEFAULT` = `CANIS_MODE_PLAY` (boots into play; `g`/`p`/`b` switch live).
- `CANIS_USE_DISPENSE_ACK` = **1** if you wired the pin-11→GPIO4 ACK line, else **0**.
- Roaming/flee/place tunables are in the ROAM + PLACE sections (defaults are fine).

Steps:
1. **[DO]** Copy `CANIS_Core/arduino_secrets.h.example` → `arduino_secrets.h`; fill WiFi
   SSID/PASS (+ token if you set one).
2. **[DO]** Flash `CANIS_Core/CANIS_Core.ino` → **ESP32** (Serial 115200).
3. **[DO]** Flash `Treat_Dispenser_CANIS/Treat_Dispenser_CANIS.ino` → **Arduino** (9600).
4. **[DO]** On the Pi: `pip install -r dashboard/requirements.txt`, then run
   `python3 dashboard/app.py` and
   `python3 vision/detect.py --directional --marker-ids <Shifu's id>`
   (`--directional` gives the left/right flee bearing).

---

## 5. Bench bring-up (WHEELS OFF THE GROUND)

Open the ESP32 Serial monitor (115200). Keys print on boot.

1. **[TEST] `c` → guided self-test.**
   - Motors: it pulses each wheel; answer y/n - a wrong wheel is fixed + saved with
     `INVL`/`INVR` (no re-wiring). yes both wheels spin the expected way.
   - Dispenser: cycles the flap; nudge with `+`/`-`/`y`. yes one treat drops cleanly.
   - Sensors: prints PASS/FAIL for 3 ultrasonics + PIR. yes all read (no "FAIL - wiring").
2. **[TEST] `d` → test-fire dispenser.** yes marker tone + one treat.
3. **[TEST] Vision:** `python3 detect.py --selftest`. yes "PASS - dog marker detected."
4. **[TEST] 3-way link:** dashboard (Pi `:5000`) shows live state + the ESP32's config.
   yes dashboard updates ~1 Hz; show the marker → dashboard flips to "dog in zone."

---

## 6. Behaviour / mode tests (still wheels-off-ground for motion)

1. **[TEST] `g` GUARD:** put a hand within ~30 cm of an ultrasonic (fake the dog) →
   **buzzer**; remove the hand → **treat** ("settled / left zone"). yes buzz on approach,
   treat on leave, **no motion toward** the hand.
2. **[TEST] `p` PLAY:** hold the marker in front of the camera → **treat + a retreat move**
   (LEFT/RIGHT/BACKWARD away from the marker side); hide it → it issues **roam legs**
   (FORWARD / turn-to-clearer-side). yes never a move *toward* the marker; obstacle side
   with a hand near an ultrasonic makes it steer the other way.
3. **[TEST] `b` PLACE:** hold the marker steadily in view → after ~6 s a **treat**, then
   another every ~45 s while held; remove it → "left his spot", timer resets, **no treat
   for leaving**. yes treats only while present + settled.
4. **[TEST] `s`** stops motion any time; **`r`** resets arousal.

---

## 7. First runs near Shifu

- **[DO]** Put the ArUco marker on his collar/harness, **as big as reasonable, facing
  outward.** Good lighting (vision drops dark/blurry frames).
- **[DO]** Wheels on the ground now. Start in **PLACE (`b`)** or **GUARD (`g`)** (calm,
  no chasing) to let him get used to it, then try **PLAY (`p`)**.
- **[DO]** **Supervise every run.** Watch his body language - a moving robot can spook a
  fearful dog. Hit **`s`** / switch to a still mode if he's stressed. `CANIS_NEVER_APPROACH`
  guarantees it never drives at him, but backing up is blind (no rear sensor) - don't run
  PLAY in a tight/cluttered corner.
- Note: it only "sees" him when his marker is **in the front camera, face-on, close-ish** - expect it to react when he comes to it, not to track him around the house.

---

## 8. Battery day - the only thing left

When the Pi power bank arrives:
1. Strap the bank underneath, plug the **Pi into the bank** instead of the wall.
2. That's it - CANIS is fully untethered. Nothing else changes (Pi is WiFi-only/isolated).

---

### Safety recap
- Bench-test failure modes with **wheels off the ground**, never with the dog in the room.
- The Arduino 1 s motion watchdog auto-stops the wheels if the link drops.
- `CANIS_NEVER_APPROACH = 1`: the robot never drives toward Shifu in any mode.
- Treat cooldown (20 s) + daily cap (30) limit treats in every mode - it can't overfeed him.
