# Encoder bring-up — a step-by-step plan (first-timer version)

Written 2026-10-04 for the rebuilt robot. Do it **after** the 6×AA pack and the power bank, with the motor battery **out**
and the robot propped up (wheels off the ground). Allow roughly an hour the first time (estimate).
Facts about the code are `[code]` (with `file:line`); facts about the hardware are `[bench]` and are reported, not verified here.

## 0. What you are doing, in plain words

Each N20 motor has a small circuit board on its back (an **encoder**) that sends a pulse every time the shaft turns a little.
The ESP32 counts the pulses, so it knows how far and how fast each wheel really turned. The safety layer for Shifu depends on that
number being right (`docs/SAFE_MOTION_CBF_SPEC.md`), which is why you measure it instead of trusting a datasheet.

Each encoder board is labelled **M1, GND, C1, C2, VCC, M2** `[bench]`.
- **M1 and M2 are the motor.** They are already on the L298N. **Do not touch them.**
- **VCC, GND, C1, C2 are the encoder.** These four wires per motor are the job.

The mobile phase stays **off** (`CANIS_MOBILE_ENABLED 0`, `canis_config.h:234`). Nothing here turns it on.

## 1. The wiring table  `[code]` `canis_config.h:248-251`

| Encoder wire | LEFT motor goes to | RIGHT motor goes to |
|---|---|---|
| **C1** (channel A) | ESP32 **D33** | ESP32 **VP** |
| **C2** (channel B) | ESP32 **D27** | ESP32 **VN** |
| **VCC** | Arduino **3V3** pin | Arduino **3V3** pin |
| **GND** | ground rail | ground rail |

- "Left" is the side on your left standing behind the robot, looking toward the dispenser end.
- **VCC must be 3.3 V, never 5 V.** The ESP32's pins are not 5 V-tolerant (`hardware/WIRING.md`). The Uno's 3V3 pin is limited to about 50 mA, which is plenty
  for two encoder boards (a few mA each, typically — not measured).
- **VP and VN** are the two labels on the ESP32 for GPIO 36 and 39. They are input-only, which is fine for an encoder.
- Both VCC wires can be twisted together into **one** jumper to the Uno's 3V3 pin; same for the two GND wires.

## 2. What to have in front of you

The four encoder wires per motor (stripped ends), jumper wires with **female** ends, tape, a marker, small scissors/strippers if you have them,
your multimeter, and a mini breadboard if you want a junction. **No soldering is required.** If you do have an iron, a soldered joint is
more reliable than tape.

## 3. Steps

### Step 1 — identify and label every wire (power OFF, USB unplugged)
For the RIGHT motor, find which harness wire goes to each pad labelled GND, C1, C2, VCC on the board (follow it from the connector
pin; or use the seller's sheet; or a continuity check from the bare tip to the pad). **Wire colours are not standard**, so never guess from colour.
Stick a bit of tape on each wire's end and write `R-C1`, `R-C2`, `R-VCC`, `R-GND`.

### Step 2 — make each wire connectable
The harness ends are thin and will not stay in a breadboard hole or a jumper socket. Pick one (all tested ideas, none needs a tool you may lack):
- **Twist** the bare end tightly onto the bare end of a jumper wire cut in half, then wrap tape around the join. Tug it.
- **Stiff-leg adapter:** wrap the bare end around a resistor leg or a straightened paper clip, tape it, and push the stiff end into the jumper's female socket.
- **Solder** if you can. A loose encoder wire silently drops counts, which corrupts the distance the safety layer trusts.
Keep each join separated from its neighbours by tape so nothing touches.

### Step 3 — wire the RIGHT encoder (USB unplugged)
In this order: **GND** to the ground rail, **VCC** to the Arduino's 3V3, **C1** to **VP**, **C2** to **VN**.
Tug every wire. Route the encoder wires **away from the motor wires** (motor noise can corrupt the signals); a little distance is enough.

### Step 4 — check the power with the meter (USB plugged in)
Meter on DC volts: **VCC to GND at the encoder should read about 3.3 V.**
- About 3.3 V: good.
- About 5 V: **stop, unplug.** VCC is on the wrong pin.
- 0 V: a power wire is loose or on the wrong pin.

### Step 5 — read the counts (no motors, no mobile mode)
1. In the Arduino IDE open `hardware/encoder_test/encoder_test.ino`, choose board **ESP32 Dev Module** and the robot ESP32's port (the CP2102), and upload.
   (This **replaces** `CANIS_Core` on the board until you flash it back in Step 8.)
2. Serial Monitor at **115200**. You should see lines like  `L=     0 (/10 = 0.0)  levels A1 B1  |  R=     0 ...`.
3. Turn the **right wheel** forward by hand, slowly.
   - **R climbs** and L stays at 0: correct.
   - **R falls when turning forward:** swap that encoder's **C1 and C2** wires (not the motor wires). Turn it again.
   - **R stays 0 but the `levels` change:** the wiring is right and the counting setup is not; tell me.
   - **R stays 0 and the right `levels` never change:** the encoder is not sending; recheck VCC/GND (Step 4) and C1/C2.
   - **Numbers jump around with the wheel still:** noise; move the encoder wires away from the motor wires.

### Step 6 — the LEFT encoder
Repeat Steps 1 to 5 for the left motor (`L-C1` to D33, `L-C2` to D27). Left forward must make **L climb**.

### Step 7 — measure counts per wheel turn  (this number matters)
Press **`z`**. Turn **one wheel exactly 10 full turns** by hand (mark the tyre with tape to count). Read the `/10` value: that is **counts per turn**.
Do it for both wheels; they should agree closely. The current config value `CANIS_ENC_COUNTS_PER_REV 1320` (`canis_config.h:256`) assumes an 11-pulse motor with a 30:1 gearbox;
your motors are 7-pulse (`docs/MOBILE_UPGRADE_STATUS.md:65`) with an unknown gear ratio, so expect a different number. Write your measured value on a piece of paper;
**do not change the config until we have checked it together** (the safety layer trusts it directly).

### Step 8 — put the robot back
Flash `CANIS_Core/CANIS_Core.ino` back onto the robot ESP32. Leave the encoders wired; the main firmware ignores them while mobile mode is off.

## 4. Not now (so nobody does it by accident)
- Do **not** set `CANIS_MOBILE_ENABLED 1`. The mobile phase waits for baseline study data and for `hardware/MOBILE_BRINGUP.md`.
- Before mobile mode: set `CANIS_WHEEL_DIAMETER_MM` to 43 and `CANIS_WHEEL_BASE_MM` to 165 (`canis_config.h:258-259`, currently 65 and 140), and enter the measured counts per turn.
- The mobile firmware **did not compile** before 2026-10-04 (a variable was used before it was declared). That is fixed: both builds compile, but the mobile code has **not been run** on hardware.

## 5. Quick fixes
| Symptom | Likely cause |
|---|---|
| Right/left count only goes one way | C2 (or C1) wire not connected |
| Count goes the wrong direction | swap that encoder's C1 and C2 |
| Count drifts with the wheel still | noise: separate from the motor wires; twist the encoder wires together |
| `levels` on VP/VN never change | encoder unpowered (VCC/GND), or C1/C2 not connected (VP/VN have no internal pull-ups) |
| ESP32 will not boot with an encoder wired | disconnect it and tell me which pin; D33 and D27 are normal pins, VP/VN are input-only |
