# CANIS

A home robot built around **classical (Pavlovian) conditioning** to address real
behavioral issues in a young Goldendoodle (Shifu) — eating things he shouldn't,
barking, climbing furniture, roaming. Cameras watch a room, detect bad behavior in
a zone, and CANIS intervenes with a **buzzer marker + treat**, rewarding the dog for
*leaving* a forbidden zone rather than punishing him for entering it.

The core constraint shaped the whole design: Shifu is **afraid of moving objects**, so
a robot that "corrects" by charging at him would traumatize him. CANIS instead
introduces movement *gradually*, over weeks/months, while measuring his fear
response. That longitudinal fear-response data is the actual point of the project.

---

## Architecture — the data-flow chain

```
  Sensors (PIR, ultrasonic)
        │
        ▼
  ┌───────────┐   UART serial    ┌───────────┐   WiFi / HTTP    ┌───────────────┐
  │  ESP32    │ ───────────────▶ │  Arduino  │                  │ Raspberry Pi  │
  │  (brain)  │   FORWARD/STOP/  │  (limbs)  │                  │ (eyes + face) │
  │           │   DISPENSE...    │           │                  │               │
  │  state    │ ◀─────────────── │ motors +  │                  │ camera/vision │
  │  machine  │      (ACKs)      │ dispenser │                  │ + dashboard   │
  └───────────┘                  └───────────┘                  └───────────────┘
        │                                                               ▲
        └───────────────── WiFi / HTTP POST /ingest ────────────────────┘
                              (state pushed up to the dashboard)
```

| Device | Role | Runs |
|--------|------|------|
| **ESP32** | The brain. Reads sensors, runs the state machine, *decides*. Talks both ways. | `State_Machine_WiFi/` |
| **Arduino Uno** | Dumb, reliable executor. Precise real-time control of motors + dispenser servo + buzzer. | `Treat_Dispenser_CANIS/` |
| **Raspberry Pi 5** | Heavy lifting neither MCU can do: OpenCV vision + the Flask dashboard. | `dashboard/` |

**Why split this way:** the Arduino is great at precise 5 V servo/motor timing — keep
it dumb. The ESP32 has WiFi + enough compute for the state machine and can coordinate
both other devices. The Pi does vision (OpenCV) and serves the web UI.

---

## Repository layout

```
CANIS/
├── README.md                       ← this file
├── arduino_secrets.h.example       credentials template (copy into networked sketches)
├── CANIS_Core/                     ESP32 — THE INTEGRATED BRAIN (use this on hardware)
│   └── CANIS_Core.ino                sensors + state machine + dispense + buzzer + WiFi + vision poll
├── Treat_Dispenser_CANIS/          Arduino Uno — treat dispenser (button + serial DISPENSE)
│   └── Treat_Dispenser_CANIS.ino
├── dashboard/                      Flask dashboard + command channel (runs on the Pi)
│   ├── app.py                        /api/state, /ingest, /detect, /command
│   ├── canis-dashboard.service       systemd unit (auto-start on boot)
│   ├── requirements.txt
│   └── templates/index.html
├── vision/                         Phase 6 — ArUco detection (runs on the Pi)
│   ├── detect.py                     marker -> POST zone; Pi Camera/USB, --test/--snap, directional labels
│   └── SETUP.md                      Pi camera setup (CSI ribbon, picamera2, sanity checks)
├── hardware/                       wiring + power reference
│   ├── WIRING.md                     every pin, every wire, power architecture, L298N
│   ├── TESTING.md                    per-subsystem hardware bring-up test guide
│   └── cad/                          OpenSCAD chassis/dispenser/shell (print AFTER prototype passes)
├── State_Machine_CANIS/            offline state-machine reference (pure logic, no WiFi)
├── State_Machine_WiFi/             networked state-machine reference (superseded by CANIS_Core)
├── Sensors_Test/                   standalone PIR + 3-ultrasonic test
├── WiFi_Scan_Test/  WiFi_Connect_Test/   ESP32 WiFi diagnostics (kept as tools)
```

> Eventual GitHub structure (`firmware/esp32/`, `firmware/arduino/`, `vision/`,
> `dashboard/`, `hardware/`) is a Phase 10 reorg — not done yet. The flat layout
> above is the current reality.

---

## The state machine (the architectural core)

Three states, driven by an **arousal** level (0–100):

| State | Meaning | Behavior |
|-------|---------|----------|
| `CALM` | Baseline, settled | Rewards *sustained* calm (after warm-up, ≥30 s apart) |
| `AROUSAL` | Something detected, watching | Monitor only — **no intervention** |
| `CORRECTION` | Bad behavior confirmed | 800 Hz disengage buzzer + orient toward zone, every 2 s |

Arousal is **rate-driven**: while *any* detection is active (local PIR/ultrasonic
**or** a remote vision call from the Pi) it **rises** at `+30/sec`; it always
**decays** at `12/sec`, so presence climbs (net `+18/sec`) and absence settles.
Crossing `20` → `AROUSAL`, crossing `70` → `CORRECTION`. Dropping back to `CALM`
is what earns a reward — CANIS rewards *leaving*, never punishes entering.

A confirmed detection holds arousal up while it lasts (so a lingering dog isn't
wrongly "rewarded for leaving"). Rewards are rate-limited: a 20 s cooldown + a
30/session cap so the hopper can't empty. Power & pinout: see
[`hardware/WIRING.md`](hardware/WIRING.md).

**The key design idea:** dropping back into `CALM` from `AROUSAL`/`CORRECTION` means the
dog disengaged / left the zone — and *that* is what gets rewarded. CANIS rewards
**leaving**, never punishes entering.

All thresholds are **empirical** — they get tuned against Shifu's real observed
behavior, not finalized in code. The same tunables live in both the `.ino` files and
`dashboard/app.py`; keep them in sync.

Right now sensors are stubbed: type `t` (trigger) / `r` (reset) into the ESP32 Serial
Monitor @ **115200** to drive the state machine by hand. Phase 4 replaces those keys
with confirmed PIR/ultrasonic reads.

---

## The dashboard

Flask app on the Pi. The ESP32 **pushes** its state to `POST /ingest`; the dashboard
serves the latest from `GET /api/state` and the browser polls it every second.

- If no ESP32 data has arrived in the last 5 s, it **falls back to a built-in
  simulator** (mirrors the `.ino` logic) so the page is never blank during a demo.
  The footer says which one you're seeing: *"live data — ESP32"* vs *"demo data —
  simulator"*.
- The ESP32 has **no wall clock**, so it sends raw counters only. The Pi watches
  `reward_count` tick up and stamps `last_reward_time` with its own clock. All
  time-of-day logic stays on the device that knows the time.

### State JSON (`/api/state`)
```json
{
  "state": "CORRECTION",
  "arousal": 75.0,
  "arousal_max": 100.0,
  "intervention_count": 1,
  "reward_count": 1,
  "last_reward_time": 1782391937.8,
  "uptime_sec": 42,
  "source": "esp32",
  "ts": 1782391937.9
}
```

---

## How to run

### Dashboard (Pi)
```bash
cd dashboard
pip3 install -r requirements.txt
python3 app.py
hostname -I            # note the Pi's IP, e.g. 192.168.1.50
```
Open `http://<pi-ip>:5000` from your phone (same WiFi). With no ESP32 connected
you'll see the simulator loop running.

### ESP32 ↔ Pi link
1. **Credentials:** copy `arduino_secrets.h.example` into each networked sketch
   folder (`CANIS_Core/`, `State_Machine_WiFi/`) as `arduino_secrets.h` and fill in:
   ```cpp
   #define SECRET_SSID    "YOUR_WIFI_NAME"      // 2.4GHz only
   #define SECRET_PASS    "YOUR_WIFI_PASSWORD"
   #define SECRET_PI_HOST "192.168.1.50"        // Pi IP from `hostname -I`
   ```
   `arduino_secrets.h` is gitignored, so credentials never get committed.
2. Pi, ESP32, and phone all on the **same WiFi**.
3. Arduino IDE → Boards Manager → install **"esp32 by Espressif"** → select your ESP32
   board → flash **`CANIS_Core`** (the full integrated brain; `State_Machine_WiFi` is
   just a simpler reference). Serial Monitor @ **115200**.
4. Tap `t` a few times → arousal climbs, dashboard footer flips to *"live data —
   ESP32"*, state card changes color, counters move. (Pins & power:
   [`hardware/WIRING.md`](hardware/WIRING.md).)

### Treat dispenser (Arduino Uno)
Standalone and bench-tested. Push the button (pin 2) → buzzer marker → servo gate
opens ~150 ms → closes. `GATE_OPEN_MS` is the main reliability knob; the narrowed
tube "throat" (one treat at the gate at a time) matters more than timing.

---

## Build status (by phase)

| Phase | What | Status |
|-------|------|--------|
| 1 | Mobility (motors + L298N) | ✅ **Hardware-tested**: L298N differential drive, both channels PWM-matched, all 5 commands, direction tuned on the metal chassis (6 V AA pack). Speeds at 200/220 for bring-up — dial down for exposure |
| 2 | Treat dispenser | ✅ Bench-tested, works |
| 3 | ESP32 ↔ Arduino serial | ✅ DISPENSE proven; FORWARD/BACKWARD/LEFT/RIGHT/STOP added (Arduino-side tested; over-link confirms in full integration) |
| 4 | PIR + ultrasonic sensors | ✅ 1-sensor proven (HW-416A PIR, HC-SR04 w/ 1 kΩ on ECHO). Expanded to **3 ultrasonics** (L/C/R) in firmware; 3-sensor positional test deferred to **post-print** (positions come from the shell paw) |
| 5 | State machine | ✅ Real outputs out (CANIS_Core.ino): dispense + CORRECTION cue + reward cooldown/cap. "Orient toward zone" now **real** — pulsed motor commands, gated by `EXPOSURE_LEVEL`, watchdog-safe |
| 6 | Vision / detection | ✅ **End-to-end on hardware**: OV5647 → OpenCV/ArUco detects marker ID 0 → directional zone posted → dashboard `/command` serves it (what the ESP32 polls). Pi Camera via picamera2; `--snap`/`--test` helpers; setup in `vision/SETUP.md` |
| 7 | Dashboard | ✅ Live ESP32→Pi link + simulator fallback; auto-starts on boot (systemd) |
| 8 | Power & reliability | 🟡 Firmware-side done; motors run on the 6 V AA pack. Buck converter in hand — **5 V dial-in for the Pi still pending** |
| 9 | Full integration test | ⬜ Needs the 3-sensor wiring (post-print) + a full-loop run |
| 10 | Documentation | 🟡 README + `hardware/WIRING.md` + `hardware/TESTING.md` + `vision/SETUP.md` |

**Running in parallel:** gradual exposure / fear-desensitization with Shifu starts as
soon as Phase 1 produces a robot that can move at all — it runs on its own
weeks/months calendar and does not wait for the build to finish.

---

## Hardware gotchas (don't skip these)

- **ESP32 ↔ Arduino serial is 3.3 V vs 5 V.** ESP32→Arduino is fine, but
  **Arduino TX (5 V) → ESP32 RX needs a level shifter or a 1k/2k voltage divider** or
  you'll damage the ESP32. (Phase 3.)
- **Use `SoftwareSerial` on the Uno** (e.g. pins 10/11) for the ESP32 link — the Uno's
  only hardware UART is shared with USB/Serial Monitor.
- **L298N needs its own battery power**, separate from the Arduino's logic supply.
  Runs on the 6 V AA pack — *not* a 9 V PP3 block (can't supply motor current).
- **Remove the L298N ENA/ENB jumper caps** to get PWM speed control from the Arduino.
  Leaving them on pins the channel to full speed and fights the PWM — this bit us
  (one channel looked "dead" until its ENB cap was off *and* pin 5 was wired solid).
  The separate **+5V-enable jumper stays ON** for a ≤12 V supply.
- **Loose power-feed terminals = weak/slow motors.** A battery wire not fully clamped
  drops voltage under load. Strip to bare copper, clamp fully, tug-test.
- **Three "motor driver" boards from the parts pile were ruled out** — none exposed
  IN1-4/ENA/ENB digital control pins. L298N is the correct part.
- **Pi camera needs the CSI adapter ribbon for the Pi 5** — now in hand. Vision runs
  the Pi Camera via `picamera2` (USB webcam still works via `detect.py --camera usb`).
  Setup: `vision/SETUP.md`.
- **Vision needs `opencv-contrib-python`** (not stock OpenCV) for ArUco marker support.
