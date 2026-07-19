# CANIS — Stationary Robot Integration (full wiring, one pass)

Build the complete stationary reward station. **Skipped (mobile-phase):** L298N,
2 motors, encoder pins, battery, buck. Power is USB (it's a plug-in station).

## 1. Power + the mandatory ground tie
- ESP32 → USB 5 V (wall charger or Mac)
- Arduino → USB 5 V (other port)
- Pi → 27 W brick (already up; dashboard auto-runs on :5000)
- **TIE GROUNDS:** jumper ESP32 GND ↔ Arduino GND. Required for the link. #1 forgotten wire.

## 2. ESP32 — sensors + brain
INVENTORY (confirmed at bench): 1 PIR + THREE ultrasonics — 2× HC-SR04 (LEFT,
RIGHT) + 1× SR04M-2 waterproof (CENTRE/forward). NO ToF. SR04M-2 is HC-SR04-
compatible (TRIG/ECHO), read natively by the firmware — nothing deferred.
| ESP32 pin | wire to | note |
|---|---|---|
| 27 | PIR OUT | 3.3 V direct |
| 32 | US LEFT TRIG (HC-SR04) | |
| 34 | US LEFT ECHO | via 1 kΩ series |
| 26 | US CENTRE TRIG (SR04M-2) | |
| 25 | US CENTRE ECHO | via 1 kΩ series |
| 14 | US RIGHT TRIG (HC-SR04) | |
| 35 | US RIGHT ECHO | via 1 kΩ series |
| 33 | buzzer + | buzzer − → GND |
| 13 | → Arduino pin 10 | command link, 3.3→5 V direct |
| 4  | ← Arduino pin 11 | ACK, via 1k/2k divider |
| VIN/5V | PIR + 3× US VCC | 5 V rail |
| GND | all sensor GND + Arduino GND | common |

## 3. Arduino — dispenser
| Arduino pin | wire to |
|---|---|
| 9 | servo signal (orange) |
| 5V | servo VCC (red) |
| GND | servo GND (brown) + common |
| 10 | ← ESP32 GPIO 13 |
| 11 | → ESP32 GPIO 4 (via divider) |

Skip: pin 2 (button), A0 (beam-break), pin 6 (2nd buzzer) — optional.

## 4. Resistors
- Each US ECHO (34/25/35): 1 kΩ in series to the ESP32 pin.
- ACK divider, pin 11 → GPIO 4:
  ```
  Arduino 11 ─[1kΩ]─┬─ ESP32 GPIO4
                    [2kΩ]
                    GND
  ```

## 5. Order: ground rail → sensors → servo → 2 link wires → sanity check.

## 6. Bring-up (assistant drives)
- ESP32 serial @115200, `c` self-test → PIR + 3 US PASS (motor step: answer n/skip).
- `d` → test-fire → marker tone + servo drops.
- Dashboard (:5000) shows live state (ESP32 already handshakes at ~1 Hz).
- Start YOLO on Pi: `cd ~/canis/optimized && python3 vision/detect_yolo.py --zone kitchen`
  → stand in view → dashboard flips "dog in zone" → reward → buzzer + treat.
- That full loop = finished stationary robot. Then start the study (commit protocol first).

Config in canis_config.h (already set): CANIS_MOBILE_ENABLED 0, CANIS_CAMERA_MODE
ONBOARD, CANIS_USE_DISPENSE_ACK 1. Firmware: CANIS_Core.ino → ESP32,
Treat_Dispenser_CANIS.ino → Arduino.
