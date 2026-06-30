# CANIS — Wiring & Power Reference

Single source of truth for every pin, wire, and power decision. Keep this in
sync when wiring changes.

---

## Power architecture

### Now (bench / development) — NO battery needed

Everything runs on USB + wall power. This is the current, correct setup.

| Device | Powered by | Also powers |
|--------|-----------|-------------|
| ESP32 (DevKit V1) | USB (laptop or any 5 V phone charger) | PIR + 3× ultrasonic VCC (from **VIN**), buzzer |
| Arduino Uno | USB (laptop) | dispenser servo + buzzer (from **5 V** pin) |
| Raspberry Pi 5 | official 5 V/5 A USB-C PSU | itself (+ USB webcam later) |

Nothing here needs a battery. The motors aren't wired (no L298N yet), so nothing
is waiting on motor power.

### When the L298N arrives (motors)

The L298N needs its **own** motor power, **separate** from the Arduino's logic
supply — they share only **GND**. Never run the motors off the Arduino's 5 V
regulator (it will brown out / overheat).

No dedicated battery yet? You can still bench-test driving:
- a spare **5 V USB power bank/charger** into the L298N motor input — the little
  yellow gearmotors spin fine on ~3–5 V through the L298N for testing, **or**
- a **4×AA holder (6 V)** if you have one.

Avoid a **9 V "block" (PP3)** battery — it cannot supply motor current.

### Real mobile robot (Phase 8, later)

Decided architecture — the Pi runs off its **own bank**, the buck powers only the
low-draw robot logic:

- **Pi 5 + camera:** its OWN USB power bank — **5 V / 3 A+ (ideally USB-C PD / 27 W)**.
  The Pi talks to the ESP32 only over WiFi (no wired link to the robot), so it's
  electrically **isolated** — its ground need NOT join the robot's. ⚠️ A weak
  5 V/2 A bank will brown-out the Pi 5 under load (same failure as a weak wall
  charger) — spec it for ≥3 A.
- **Motors:** motor pack (6×AA ≈ 9 V, or a 2S Li-ion 7.4 V) straight into the
  L298N `+12V`. **Not** a 9 V PP3 block — can't source motor current.
- **ESP32 + Arduino + sensors:** a **buck converter** (LM2596) off the motor
  battery, set to 5 V → Arduino `5V` pin + ESP32 `VIN/5V` + sensor VCC. These are
  low-draw (~0.3–0.6 A), so the LM2596 is fine here. It is **NOT** used for the Pi
  (the Pi's ~5 A appetite is more than an LM2596 can hold — that's why the Pi gets
  its own bank).
- ⚠️ **Set the buck to 5.0–5.1 V with a multimeter BEFORE connecting anything to its output.**

### ⚠️ The one rule that matters

**All ROBOT-SIDE grounds must be common** — ESP32 GND, Arduino GND, sensor GND,
L298N GND, buck −, and the motor battery −. Logic and motor voltages may differ,
but ground is shared. *(The Pi is the exception: on its own bank and WiFi-only, it
needs no shared ground.)*

---

## ESP32 (DevKit V1) — pin map

| GPIO | Use | Dir | Note |
|------|-----|-----|------|
| 27 | PIR `OUT` | IN (pulldown) | reads LOW if PIR unplugged → no phantom motion |
| 26 | Ultrasonic **CENTRE** `TRIG` | OUT | original sensor |
| 25 | Ultrasonic **CENTRE** `ECHO` | IN | **via 1 kΩ series resistor** (5 V→3.3 V) |
| 32 | Ultrasonic **LEFT** `TRIG` | OUT | |
| 34 | Ultrasonic **LEFT** `ECHO` | IN (input-only) | **via 1 kΩ series resistor** |
| 14 | Ultrasonic **RIGHT** `TRIG` | OUT | freed from the old Serial2 RX |
| 35 | Ultrasonic **RIGHT** `ECHO` | IN (input-only) | **via 1 kΩ series resistor** |
| 33 | Buzzer `+` | OUT | 800 Hz "disengage" cue |
| 13 | Serial2 TX → Arduino pin 10 | OUT | command link to dispenser/limbs |
| VIN | 5 V out (from USB) | — | powers PIR + all three ultrasonic VCC |
| GND | common ground | — | |

**Three ultrasonics** (2× HC-SR04 + 1× waterproof SR04M-2) are mounted
left/centre/right and fired **sequentially** in firmware — a shared/parallel
trigger makes their echoes cross-talk. The trio also gives CANIS a *local
bearing* (which sensor is closest → which way to orient), independent of the
Pi's vision. Each `ECHO` needs **its own 1 kΩ series resistor**.

⚠️ The **SR04M-2 is waterproof but has a ~25 cm blind zone** and isn't a perfect
HC-SR04 drop-in — put it on the longest-range position and keep `ZONE_CM` (30 cm)
just above its blind spot. Set `NUM_US = 2` in firmware to run HC-SR04s only.

Serial2 RX is now `-1` (unused) in firmware so the UART no longer claims GPIO 14.

**Free on the accessible side:** GPIO 36, 39 (input-only). *(Motors do NOT use
ESP32 pins — the Arduino drives the L298N; see below.)*

**Powered by:** USB.

---

## Arduino Uno — pin map

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

### L298N motor control (implemented)

The **Arduino** drives the L298N; the ESP32 just sends `FORWARD` / `BACKWARD` /
`LEFT` / `RIGHT` / `STOP` over the existing serial link, so **no new ESP32 pins
are needed**. 2-motor differential drive: left wheel = channel A, right = B,
plus the castor.

| L298N | Arduino pin | Note |
|-------|-------------|------|
| ENA | 3 | PWM — left (A) speed |
| IN1 | 4 | left direction |
| IN2 | 7 | left direction |
| IN3 | 8 | right direction |
| IN4 | 12 | right direction |
| ENB | 5 | PWM — right (B) speed |
| OUT1/OUT2 | → left motor | screw terminals |
| OUT3/OUT4 | → right motor | screw terminals |
| +12V (motor V+) | motor battery **+** | **NOT** the Arduino 5 V |
| GND | common ground | shared with everything |
| +5V jumper | leave **ON** for ≤12 V | onboard reg powers L298N logic |

**Motors** are brushed DC can motors on red/black leads ending in a 2-pin
quick-disconnect plug. The plug doesn't fit the L298N, so **cut it off, strip
~6 mm, and screw the bare leads into the OUT terminals** — no soldering.
Polarity is free: if a wheel spins backwards, swap its two OUT wires (or flip
`inA`/`inB` in `setMotor`).

**Noise caps:** solder a **0.1 µF (100 nF) ceramic across each motor's two
terminals** — brushed motors throw electrical noise that can glitch/reset the
MCUs.

**Safety watchdog:** movement is **not latched**. The ESP32 re-sends the move
command every ~500 ms; the Arduino auto-`STOP`s within ~1 s if commands stop
(`MOTION_TIMEOUT_MS`). So a dropped link halts the robot instead of letting it
run at the dog. Physical movement is also gated behind `EXPOSURE_LEVEL` (default
0 = no motion) so it ramps up gradually as Shifu desensitises.

---

## Detail: the ultrasonic ECHO protection

```
SR04 ECHO (5V) ──[ 1 kΩ ]──→ ESP32 GPIO 25
```
The 1 kΩ in series with the ESP32's internal clamp diode limits current to
~1.7 mA, holding the pin at a safe ~3.3 V. (A 1 k/2 k two-resistor divider also
works but isn't necessary.)

---

## Quick reference: what's wired where

```
ESP32 ──(GPIO13 → pin10, +GND)── Arduino ──(L298N, later)── motors
  │                                  │
  ├─ PIR (27), ultrasonic (26/25)    ├─ servo (9), buzzer (6), button (2)
  └─ buzzer (33), WiFi → Pi          └─ powered by USB
       │
       └─ Pi (dashboard + vision), powered by wall PSU
```
