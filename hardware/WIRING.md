# CANIS - Wiring & Power Reference

Single source of truth for every pin, wire, and power decision. Keep this in
sync when wiring changes. Pin names track `canis_config.h` (the
firmware single source of truth) - if a `#define` and this table disagree, the
`#define` wins and this file is stale.

This file now covers **two builds** from the same wiring:

- **Stationary** (`CANIS_MOBILE_ENABLED = 0`, the SAFE default that ships) - everything below the "Mobile drivetrain" heading is simply not populated. No
  encoders, no IMU, no ToF, no new pins. Byte-for-byte the pre-upgrade robot.
- **Mobile** (`CANIS_MOBILE_ENABLED = 1`) - adds 2× quadrature encoders on the
  ESP32, an IMU + VL53LX ToF on I²C, and swaps both bare DC motors for encoder
  gearmotors. Everything stationary keeps working unchanged; the mobile parts
  are *additions*, never replacements. See `hardware/MOBILE_BRINGUP.md` for the
  step-by-step "motors arrived, do exactly this" procedure.

---

## Power architecture

### Now (bench / development) - NO battery needed

Everything runs on USB + wall power. This is the current, correct setup.

| Device | Powered by | Also powers |
|--------|-----------|-------------|
| ESP32 (DevKit V1) | USB (laptop or any 5 V phone charger) | PIR + 3× ultrasonic VCC (from **VIN**), buzzer, **encoders (from 3V3)** |
| Arduino Uno | USB (laptop) | dispenser servo + buzzer (from **5 V** pin) |
| Raspberry Pi 5 | official 5 V/5 A USB-C PSU | itself (+ camera) |

Nothing here needs a battery for the stationary study. The mobile drivetrain
needs motor power (below) only when you flip `CANIS_MOBILE_ENABLED`.

### When the L298N drives motors

The L298N needs its **own** motor power, **separate** from the Arduino's logic
supply - they share only **GND**. Never run the motors off the Arduino's 5 V
regulator (it will brown out / overheat).

No dedicated battery yet? You can still bench-test driving:
- a spare **5 V USB power bank/charger** into the L298N motor input - the little
  gearmotors spin fine on ~3–5 V through the L298N for testing, **or**
- a **4×AA holder (6 V)** if you have one.

Avoid a **9 V "block" (PP3)** battery - it cannot supply motor current.

### Real mobile robot (later)

Decided architecture - the Pi runs off its **own bank**, the buck powers only the
low-draw robot logic:

- **Pi 5 + camera:** its OWN USB power bank - **5 V / 3 A+ (ideally USB-C PD / 27 W)**.
  The Pi talks to the ESP32 only over WiFi (no wired link to the robot), so it's
  electrically **isolated** - its ground need NOT join the robot's. Warning: A weak
  5 V/2 A bank will brown-out the Pi 5 under load - spec it for ≥3 A.
- **Motors:** motor pack (6×AA ≈ 9 V, or a 2S Li-ion 7.4 V) straight into the
  L298N `+12V`. **Not** a 9 V PP3 block - can't source motor current.
- **ESP32 + Arduino + sensors:** a **buck converter** (LM2596) off the motor
  battery, set to 5 V → Arduino `5V` pin + ESP32 `VIN/5V` + sensor VCC. These are
  low-draw (~0.3–0.6 A), so the LM2596 is fine here. It is **NOT** used for the Pi.
- Warning: **Set the buck to 5.0–5.1 V with a multimeter BEFORE connecting anything to its output.**

### Warning: The one rule that matters

**All ROBOT-SIDE grounds must be common** - ESP32 GND, Arduino GND, sensor GND,
L298N GND, **encoder GND, IMU GND, ToF GND**, buck −, and the motor battery −.
Logic and motor voltages may differ, but ground is shared. *(The Pi is the
exception: on its own bank and WiFi-only, it needs no shared ground.)*

---

## ESP32 (DevKit V1) - pin map

> **As built, 2026-10 (the `#define`s in `canis_config.h` and the constants in `CANIS_Core.ino` win):**
> the PIR is **removed** (`PIR_PIN = -1`) and the ESP32 buzzer is **not wired** (`BUZZER_PIN = -1`; the
> buzzer is on the Uno's pin 6 behind a 220 ohm resistor). The encoders use **remapped pins** because the
> breadboard covers the GPIO 16-19 / 3V3 side: left A/B = **33 / 27**, right A/B = **36 / 39**
> (36/39 are input-only, no pull-ups). Encoder VCC comes from the **Uno's 3V3** pin. The rows below show
> the original WROOM plan.

| GPIO | Use | Dir | Note |
|------|-----|-----|------|
| (27) | PIR `OUT` (removed) | - | the PIR is gone; GPIO 27 now carries the CENTRE ultrasonic `TRIG` (row below) |
| 27 | Ultrasonic **CENTRE** `TRIG` | OUT | **as-built 2026-10-11** (the original pin 26 is now the left encoder's channel B) |
| 25 | Ultrasonic **CENTRE** `ECHO` | IN | **via 1 kΩ series resistor** (5 V→3.3 V) |
| 32 | Ultrasonic **LEFT** `TRIG` | OUT | |
| 34 | Ultrasonic **LEFT** `ECHO` | IN (input-only) | **via 1 kΩ series resistor** |
| 14 | Ultrasonic **RIGHT** `TRIG` | OUT | freed from the old Serial2 RX |
| 35 | Ultrasonic **RIGHT** `ECHO` | IN (input-only) | **via 1 kΩ series resistor** |
| 33 | Buzzer `+` | OUT | 800 Hz "disengage" cue |
| 13 | Serial2 TX → Arduino pin 10 | OUT | command link to dispenser/limbs |
| **18** | **Encoder LEFT `A`** (`CANIS_ENC_L_A_PIN`) | IN | quadrature ch A, PCNT - MOBILE only |
| **19** | **Encoder LEFT `B`** (`CANIS_ENC_L_B_PIN`) | IN | quadrature ch B, PCNT - MOBILE only |
| **16** | **Encoder RIGHT `A`** (`CANIS_ENC_R_A_PIN`) | IN | quadrature ch A, PCNT - MOBILE only |
| **17** | **Encoder RIGHT `B`** (`CANIS_ENC_R_B_PIN`) | IN | quadrature ch B, PCNT - MOBILE only |
| **21** | **I²C `SDA`** | I/O | IMU + VL53LX ToF shared bus - MOBILE only |
| **22** | **I²C `SCL`** | I/O | IMU + VL53LX ToF shared bus - MOBILE only |
| VIN | 5 V out (from USB) | - | powers PIR + all three ultrasonic VCC |
| 3V3 | 3.3 V out | - | powers **encoders, IMU, ToF** (see Warning: below) |
| GND | common ground | - | |

**Three ultrasonics** (2× HC-SR04 + 1× waterproof SR04M-2) are mounted
left/centre/right and fired **sequentially** in firmware. Each `ECHO` needs
**its own 1 kΩ series resistor**. Warning: The **SR04M-2 has a ~25 cm blind zone**;
put it on the longest-range position. To run without it, set `CANIS_US_CENTRE_ENABLED 0`
in `canis_config.h` (the firmware then skips the CENTRE sensor). Do **not** use `CANIS_NUM_US = 2`:
the arrays are ordered LEFT, CENTRE, RIGHT, so that would drop the RIGHT sensor, not the SR04M-2.

Serial2 RX is `-1` (unused) in firmware so the UART no longer claims GPIO 14.

**Free on the accessible side after the mobile additions:** GPIO 36, 39
(input-only). *(Motors do NOT use ESP32 pins - the Arduino drives the L298N; the
ESP32 only reads the encoders and sends velocity words over the existing link.)*

**Powered by:** USB.

### Warning: Encoder / I²C voltage - read before wiring

The ESP32 GPIO are **3.3 V and NOT 5 V-tolerant**. Power the encoder logic and
both I²C devices from the ESP32 **3V3** pin, not VIN/5 V, so their signal lines
swing 0–3.3 V. If a specific encoder or IMU board is 5 V-only on its outputs,
put it behind a level shifter - do **not** wire a 5 V signal straight to a GPIO.
The motor windings themselves are still driven by the L298N at motor voltage;
only the encoder's little PCB runs on 3.3 V.

---

## Mobile drivetrain - encoder gearmotors (MOBILE only)

Two encoder gearmotors **replace** the two bare DC can-motors. The **motor
windings** wire to the L298N exactly as the old motors did (below). The new part
is the **encoder harness**: each motor's encoder PCB has 4 wires - VCC, GND, and
two Hall channels A/B.

| Encoder wire | Left motor → ESP32 | Right motor → ESP32 |
|---|---|---|
| VCC (3.3 V) | 3V3 | 3V3 |
| GND | GND (common) | GND (common) |
| channel A | GPIO **18** (`ENC_L_A_PIN`) | GPIO **16** (`ENC_R_A_PIN`) |
| channel B | GPIO **19** (`ENC_L_B_PIN`) | GPIO **17** (`ENC_R_B_PIN`) |

- **Quadrature is required** (both A and B), not a single-channel tacho - B
  gives the *sign* (direction) that the odometry and the CBF depend on. A
  speed-only encoder cannot tell "dog approaching" from "robot approaching."
- Counting is done in **hardware** by the ESP32 **PCNT** peripheral (zero CPU
  cost) - this is why the encoders land on the ESP32, not the Uno, keeping the
  Uno a dumb, certifiable executor.
- `CANIS_ENC_COUNTS_PER_REV = 1320` (4× decode). **Verify this on your motor**
  (see MOBILE_BRINGUP §4) - a wrong count silently corrupts every distance the
  barrier trusts.
- **WROVER-board caveat:** WROVER modules burn GPIO 16/17 for PSRAM. On a WROVER
  board, remap the RIGHT encoder to the input-only pins 36/39 and change
  `CANIS_ENC_R_A_PIN`/`CANIS_ENC_R_B_PIN` to match (encoders are inputs, so
  input-only pins are fine). The DevKit V1 (WROOM) used here has 16/17 free - no remap needed.

### If a wheel counts backward

If a wheel drives the correct direction but its encoder count runs the wrong
way (odometry says "reverse" when it rolls forward), **swap that motor's A and B
encoder wires** - do not swap the motor power leads (that would fix the count but
reverse the drive). If the wheel *drives* backward, swap its two L298N OUT leads
(as with the old motors); the encoder then follows correctly.

---

## VL53LX ToF + IMU - I²C bus (MOBILE only)

Both sit on the ESP32's I²C bus (SDA = 21, SCL = 22), sharing VCC(3V3)/GND. They
have different addresses, so one bus carries both.

| Device | Signal | ESP32 pin | Note |
|---|---|---|---|
| VL53LX ToF | SDA / SCL | 21 / 22 | robot→dog range (`tof_m`). `-1.0` = no valid return |
| VL53LX ToF | VCC / GND | 3V3 / GND | mount facing FORWARD, level, at the dog's standing height |
| IMU (MPU-6050 / BNO055-class) | SDA / SCL | 21 / 22 | yaw heading (`heading_deg`), corrects odometry drift |
| IMU | VCC / GND | 3V3 / GND | mount flat, axes aligned to the chassis; note orientation |

- Add **4.7 kΩ pull-ups** to 3V3 on SDA and SCL if neither breakout already has
  them (most VL53LX/IMU breakouts do - check before adding a second pair).
- The ToF is the **safety** range signal; the camera only *gates* which ToF
  return is "the dog" vs a wall. Keep its aperture clear and forward-facing.
- Heading sign convention (contract §1.1): `heading_deg` is **clockwise-positive**
  in `[0, 360)`; `odom_omega` is **+ for counter-clockwise / left turn**. The
  firmware/estimator reconcile the two - mount the IMU consistently and record
  which way is "front."

---

## Arduino Uno - pin map

| Pin | Use | Note |
|-----|-----|------|
| 9 | Servo signal | dispenser gate |
| 6 | Buzzer | reward marker (2000 Hz) |
| 2 | Button | `INPUT_PULLUP`, manual dispense |
| 10 | SoftwareSerial RX ← ESP32 GPIO 13 | command link |
| 11 | SoftwareSerial TX | unused |
| 5V | servo + buzzer power | from USB |
| GND | common ground | shared with ESP32 |

**Powered by:** USB.

### L298N motor control (unchanged pins; new `VEL` verb)

The **Arduino** drives the L298N. In the stationary build it acts on the legacy
`FORWARD/BACKWARD/LEFT/RIGHT/STOP` words. In the mobile build the ESP32 sends the
**new `VEL <pwmL> <pwmR>` verb** on the *same* serial link - the Uno maps sign →
IN1..IN4 direction and magnitude → ENA/ENB PWM. **No new L298N/Arduino pins.**

| L298N | Arduino pin | Note |
|-------|-------------|------|
| ENA | 3 | PWM - left (A) speed |
| IN1 | 4 | left direction |
| IN2 | 7 | left direction |
| IN3 | 8 | right direction |
| IN4 | 12 | right direction |
| ENB | 5 | PWM - right (B) speed |
| OUT1/OUT2 | → left motor **windings** | screw terminals |
| OUT3/OUT4 | → right motor **windings** | screw terminals |
| +12V (motor V+) | motor battery **+** | **NOT** the Arduino 5 V |
| GND | common ground | shared with everything |
| +5V jumper | leave **ON** for ≤12 V | onboard reg powers L298N logic |

**Encoder-gearmotor windings:** the two motor-power leads screw into the OUT
terminals exactly like the old motors - the encoder's 4-wire harness is separate
and goes to the ESP32 (above). Polarity is free: if a wheel *drives* backward,
swap its two OUT wires; if only the *count* is inverted, swap the encoder A/B.

**Noise caps:** solder a **0.1 µF (100 nF) ceramic across each motor's two
winding terminals** - brushed motors throw electrical noise that can glitch the
MCUs and the encoder edges.

**Safety watchdog (LAW, unchanged):** movement is **not latched**. The ESP32
re-sends the move word every ~500 ms (`CANIS_MOVE_PULSE_MS`); the Arduino
auto-`STOP`s within ~1 s if words stop (`MOTION_TIMEOUT_MS = 1000`). This applies
to `VEL` identically. No one may lengthen or bypass it. Watchdog chain,
fastest-first:

| Layer | Trigger | Action |
|---|---|---|
| Pi/dashboard | controller stale > `CANIS_MOTION_CMD_TTL_MS` (600 ms) | serves `STOP` |
| ESP32 | `/motion` poll stale > 600 ms | sends `STOP` |
| Uno | no motion word for 1000 ms | cuts motor PWM itself |

Motion is also gated behind `CANIS_EXPOSURE_DEFAULT` (0 = no motion) and, in the
mobile build, the CBF safe-motion layer - the barrier can only ever be *more*
conservative than exposure already allows (`docs/SAFE_MOTION_CBF_SPEC.md`).

---

## Detail: the ultrasonic ECHO protection

```
SR04 ECHO (5V) ──[ 1 kΩ ]──→ ESP32 GPIO 25
```
The 1 kΩ in series with the ESP32's internal clamp diode limits current to
~1.7 mA, holding the pin at a safe ~3.3 V.

---

## Quick reference: what's wired where

```
ESP32 ──(GPIO13 → pin10, +GND)── Arduino ──(L298N)── motor windings
  │                                  │
  ├─ ultrasonic (27/25,    ├─ servo (9), buzzer (6), button (2)
  │   32/34, 14/35), buzzer (33)     └─ L298N 3/4/7/8/12/5, powered by USB
  │
  ├─ MOBILE: encoders L 18/19, R 16/17 (PCNT, 3V3)
  ├─ MOBILE: IMU + VL53LX ToF on I²C 21/22 (3V3)
  └─ WiFi → Pi (dashboard + vision + CBF controller), own PSU/bank
```
