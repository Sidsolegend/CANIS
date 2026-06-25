# CANIS — Wiring & Power Reference

Single source of truth for every pin, wire, and power decision. Keep this in
sync when wiring changes.

---

## Power architecture

### Now (bench / development) — NO battery needed

Everything runs on USB + wall power. This is the current, correct setup.

| Device | Powered by | Also powers |
|--------|-----------|-------------|
| ESP32 (DevKit V1) | USB (laptop or any 5 V phone charger) | PIR + ultrasonic VCC (from **VIN**), buzzer |
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

- Motor pack: 6×AA (9 V) or a 2S Li-ion (7.4 V) into the L298N.
- **Buck converter** (LM2596) to give the Pi a clean 5 V off the battery.
- Everything shares ground.

### ⚠️ The one rule that matters

**ALL grounds must be common** — ESP32 GND, Arduino GND, sensor GND, L298N GND,
and the motor battery −. Logic and motor voltages may differ, but ground is shared.

---

## ESP32 (DevKit V1) — pin map

| GPIO | Use | Dir | Note |
|------|-----|-----|------|
| 27 | PIR `OUT` | IN (pulldown) | reads LOW if PIR unplugged → no phantom motion |
| 26 | Ultrasonic `TRIG` | OUT | |
| 25 | Ultrasonic `ECHO` | IN | **via 1 kΩ series resistor** (5 V→3.3 V protection) |
| 33 | Buzzer `+` | OUT | 800 Hz "disengage" cue |
| 13 | Serial2 TX → Arduino pin 10 | OUT | command link to dispenser/limbs |
| 14 | Serial2 RX | — | unused (we only send); reserved, unwired |
| VIN | 5 V out (from USB) | — | powers PIR + ultrasonic VCC |
| GND | common ground | — | |

**Free on the accessible side:** GPIO 32 (output-capable). GPIO 34/35/36/39 are
input-only. *(Motors do NOT use ESP32 pins — see below.)*

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

### Planned L298N control (when it arrives)

The **Arduino** drives the L298N — the ESP32 just sends `FORWARD`/`STOP`/`TURN`
commands over the existing serial link, so **no new ESP32 pins are needed**.
All of these Arduino pins are currently free:

| L298N | Arduino pin | Note |
|-------|-------------|------|
| ENA | 3 | PWM — motor A speed |
| IN1 | 4 | motor A direction |
| IN2 | 7 | motor A direction |
| IN3 | 8 | motor B direction |
| IN4 | 12 | motor B direction |
| ENB | 5 | PWM — motor B speed |
| OUT1/OUT2 | → motor A | |
| OUT3/OUT4 | → motor B | |
| +12V (motor V+) | motor battery **+** | **NOT** the Arduino 5 V |
| GND | common ground | shared with everything |

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
