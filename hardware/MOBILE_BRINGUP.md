# CANIS - Mobile Bring-Up Guide ("motors arrived, do exactly this")

This is the concrete checklist for turning the stationary CANIS sentry into the
CBF-certified mobile robot, the day the encoder gearmotors show up. Do the steps
**in order**. Each one has a pass bar; do not proceed past a step that failed.

**Read-first companions (do not duplicate - this file only tells you what to
DO):**
- `hardware/WIRING.md` - every pin and power decision (mobile additions included).
- `canis_config.h` - the firmware single source of truth for every
  constant named here (pins, geometry, caps, mode switch).
- `docs/INTERFACE_CONTRACT.md` - the wire-format LAW (`/telemetry`, `/motion`,
  `VEL`, detection).
- `docs/SAFE_MOTION_CBF_SPEC.md` - WHY the barrier layer exists and its math.
- `docs/MOBILE_UPGRADE_STATUS.md` - the A/B/C camera decision and timing.

---

## The two golden rules (violate neither)

1. **WHEELS OFF THE GROUND until the very last step.** Every firmware flash,
   every encoder check, every first `MOVE`, every CBF sim runs with the robot up
   on a box, wheels spinning free. You put wheels on the floor only at the
   graduated exposure ladder, and even then with **no dog present** first.
2. **NEVER failure-test with the dog present.** Pulling the ToF, killing WiFi,
   spoofing distances, first-motion tests - all of it happens in an empty room.
   The dog only ever meets a system that has already passed every fail-safe
   test. Welfare precedence outranks every schedule.

`CANIS_MOBILE_ENABLED` stays **0** (the shipped default) until Step 4. With it at
0 the robot is byte-for-byte the stationary sentry - you can do Steps 1–3
(mount, wire, flash) at any time without changing the robot's behavior.

---

## Two operating modes (what this guide is switching between)

| | Stationary (`CANIS_MOBILE_ENABLED = 0`, default) | Mobile (`= 1`) |
|---|---|---|
| Motion source | legacy roam/orient/flee words, gated by exposure | CBF pipeline: Pi estimator + comfort margin + QP → `MOVE v ω` → `VEL` |
| ESP32 extras | none | reads encoders, streams `/telemetry` @10 Hz, polls `/motion` |
| Hardware needed | none new | 2× encoder gearmotors, IMU, VL53LX ToF |
| Fail-safes | exposure gate, never-approach, Uno watchdog, caps | **all of the above PLUS** the distance barrier - additions only |

Everything the stationary study relies on keeps working in mobile mode; the CBF
layer only ever *adds* conservatism. Degradation is one-way toward stationary:
any sensor/compute loss ⇒ no distance-reducing command.

---

## Three camera options behind `CANIS_CAMERA_MODE` (pick one)

All three are implemented behind one switch; you choose by setting, not
rewriting. Per `docs/MOBILE_UPGRADE_STATUS.md §5b`:

| Mode | `CANIS_CAMERA_MODE` / env | Entry point | Use it |
|---|---|---|---|
| **A** onboard | `CANIS_CAMERA_ONBOARD` / `onboard` | `detect_yolo.py` | **RIGHT NOW** (this week's stationary study) - zero new hardware |
| **C** fixed room | `CANIS_CAMERA_FIXED_ROOM` / `fixed_room` | `detect_fixed.py` | **AT MOBILE** (single-room) - most reliable, lightest robot, marker-on-robot |
| **B** esp32cam stream | `CANIS_CAMERA_ESP32CAM_STREAM` / `esp32cam_stream` | `detect_yolo.py --stream-url …` | **LONG-TERM** - self-perceiving, room-portable; streaming is flaky |

One-liner: **A to start, C to go mobile, B for the ambitious long game.** This
guide brings you up on **A** (already running) and then wires the drivetrain and
switches to **C** for the mobile phase. Mode-B/-C hardware extras are Step 6.

Keep the Python env string and the `#define` in agreement per deployment; they
are two mirrors of the same choice (a header can't be imported by Python).

---

## Step 1 - Mount both encoder gearmotors (mirror-image, level)

**Parts:** 2× encoder gearmotor + wheel, 2× motor bracket, short Meccano adapter
strips, M3 bolts/nuts, the existing printed castor.

1. **Mirror-image, one axle line.** The two motors are **not** mounted
   identically - they face **opposite** directions so their output shafts are
   colinear and both wheels sit on one axle line. Bolt each motor to its bracket,
   then bracket to the Meccano grid via a short adapter strip. Left and right
   brackets are the same part, flipped. A symmetric mirror mount = clean
   differential-drive kinematics (`SAFE_MOTION_CBF_SPEC §7`); an offset or
   toed-in mount silently biases every heading estimate.
2. **Axle height by hole-row.** The wheel is 65 mm diameter, so the axle center
   must sit **~32.5 mm above the floor** for the wheel to just touch. Meccano
   holes are on a 12.7 mm (½") pitch, so pick the bracket hole-row that lands the
   shaft nearest 32.5 mm, then fine-tune with the adapter strip (shift up/down a
   row, or add a washer/spacer). Do **both** motors on the *same* row so the axle
   is level side-to-side.
3. **Level against the castor.** Measure the printed castor's total height
   (mount face to floor contact). Set the motor axle height so that, with wheels
   and castor both touching, the **deck sits level** (bubble level or eyeball
   against a tabletop). If the deck tips back, the castor is too tall → raise the
   axle a hole-row (or shim the castor down); if it tips forward, the reverse.
4. **Wheels press on.** The kit's 65 mm wheels press straight onto the
   double-D shaft - no hub. Confirm each wheel is fully seated and spins true
   (no wobble); a wobbling wheel adds odometry noise.
5. **Confirm track width.** Measure wheel-center to wheel-center and compare to
   `CANIS_WHEEL_BASE_MM = 140`. If yours differs, **measure and update the
   `#define`** before trusting any odometry - a wrong track width corrupts `ω`
   and the whole pose estimate.

**Pass bar:** both wheels on one level axle line, mirror-image brackets, deck
level on wheels+castor, wheels spin true, measured track width matches (or
`#define` updated to match).

---

## Step 2 - Wiring (motors → L298N, encoders → ESP32, ToF/IMU → I²C)

Full pin map is in `WIRING.md`; this is the mobile delta only. **Do this with
everything powered off.**

1. **Motor windings → L298N** (unchanged from the old motors): left motor
   windings → OUT1/OUT2, right → OUT3/OUT4. Motor pack **+** → `+12V`, pack **−**
   → L298N GND. **L298N GND ↔ Arduino GND ↔ ESP32 GND** (one common ground).
   5 V jumper ON. Solder a **0.1 µF ceramic across each motor's two winding
   terminals** (noise).
2. **Encoders → ESP32** (the new harness): each encoder PCB has VCC/GND/A/B.
   - Warning: **Power encoder VCC from the ESP32 `3V3` pin, not 5 V** - ESP32 GPIO are
     3.3 V and **not** 5 V-tolerant. (WROVER caveat: 16/17 are PSRAM there; use a
     DevKit V1 / WROOM, or remap the right encoder to 36/39 and edit the pins.)
   - Left A → GPIO **18**, Left B → GPIO **19** (`CANIS_ENC_L_A_PIN`/`_B_PIN`).
   - Right A → GPIO **16**, Right B → GPIO **17** (`CANIS_ENC_R_A_PIN`/`_B_PIN`).
   - Encoder GND → common GND.
3. **IMU + VL53LX ToF → I²C:** both share SDA = GPIO **21**, SCL = GPIO **22**,
   VCC = 3V3, GND = common. Add 4.7 kΩ pull-ups to 3V3 on SDA/SCL only if neither
   breakout already has them. Mount the **ToF facing FORWARD**, level, at the
   dog's standing height; mount the **IMU flat**, axes aligned to the chassis,
   and note which way is "front."

**Pass bar:** continuity/no-shorts check with a multimeter; every ground common;
encoder and I²C VCC reading ~3.3 V (not 5 V) before any logic board is trusted.

---

## Step 3 - Flash the updated ESP32 + Uno firmware

Both `.ino` sketches gained mobile code that only activates at
`CANIS_MOBILE_ENABLED = 1`; flashing them with the flag still **0** changes
nothing observable - do it now so the binaries are current.

1. **Uno** (`Treat_Dispenser_CANIS/…​.ino`): adds the `VEL <pwmL> <pwmR>` verb
   (sign→direction, magnitude→PWM) on the existing 9600 link. The 1 s
   `MOTION_TIMEOUT_MS` watchdog is **unchanged and supreme**. Verify + upload,
   Serial Monitor @ 9600.
2. **ESP32** (`CANIS_Core/CANIS_Core.ino`): adds encoder PCNT reads, odometry,
   `/telemetry` streaming, and the `/motion` → `VEL` relay - all gated behind the
   flag. `canis_config.h` is symlinked into `CANIS_Core/`; edit the root file.
3. **Compile-time guards will catch bad edits loudly.** Mobile builds require
   `CANIS_NEVER_APPROACH = 1`, `D_MIN < D_MAX`, `MOTION_CMD_TTL_MS < 1000`,
   `ODOM_STREAM_HZ ≥ 10`, `CONTROL_RATE_HZ ≥ ODOM_STREAM_HZ`, and a valid camera
   mode / marker id. If any fails, the build stops with the `#error` - fix the
   config, don't force it.

**Pass bar:** both sketches compile and upload clean; with the flag still 0 the
robot behaves exactly as the stationary study left it (Test 1–4 in `TESTING.md`
still pass).

---

## Step 4 - Software: enable mobile, then verify UP ON A BOX

Now the actual mobile bring-up. **Wheels off the ground for this entire step.**

### 4a. Set the flag in both places

Mobile is two mirrors of one choice - the firmware flag *and* the Pi
controller's env must agree:

1. Firmware: set `CANIS_MOBILE_ENABLED 1` in `canis_config.h`,
   re-flash the ESP32 (Step 3). Confirm `NEVER_APPROACH` is still `1` (the
   compile guard enforces it).
2. Pi: export `CANIS_MOBILE_ENABLED=1` in the controller's environment
   (`control/config.py` mirrors it via env, same pattern as
   `CANIS_CAMERA_MODE`). Leave it **unset** to keep the controller in
   estimation/reward-only mode with zero motion risk.

### 4b. Pick the camera mode

For the mobile phase, recommendation on file is **C (fixed room)**:

```
# fixed room camera (Option C)
CANIS_CAMERA_MODE=fixed_room   # documents the deployment
python3 detect_fixed.py --zone kitchen --cam-height 2.0 --tilt 30 \
        --marker-id 17 --marker-height 0.18 --token "$CANIS_TOKEN"
```

(Mode A stays valid if you want the robot's-eye Pi-local camera for now; mode B
is Step 6. `detect_yolo.py` launched with `CANIS_CAMERA_MODE=fixed_room`
redirects you to `detect_fixed.py`.)

### 4c. Run the controller self-test (hardware-free)

Before any motor moves, prove the safety math on the Pi:

```
cd ~/CANIS
python3 control/motion_controller.py --selftest
```

**Pass bar: 38/38 checks PASS.** This asserts the headline invariant three ways
(closed-form QP fuzz `h ≤ 0 ⟹ v*·cosβ ≤ 0`, a closed-loop sim that settles at
the boundary with zero distance-reducing commands, and dropout scenarios). It is
stdlib-only and touches no hardware. **Do not proceed if this fails.**

### 4d. Verify encoder counts (wheels off, by hand)

Bring up the ESP32 telemetry (dashboard running, `/telemetry` reachable) and
watch `enc_l`/`enc_r`.

1. Rotate the **left** wheel forward by hand → `enc_l` climbs; backward → it
   falls. Repeat for the **right**. If a count runs the wrong way, **swap that
   encoder's A/B wires** (not the motor leads).
2. **Calibrate counts-per-rev:** rotate one wheel *exactly* 10 full turns by hand
   and read the count delta; `delta / 10` should be ≈ `CANIS_ENC_COUNTS_PER_REV`
   (1320). If it's materially off, update the `#define` to the measured value - the barrier trusts this number directly.

**Pass bar:** both counts increase on forward rotation, decrease on reverse, and
counts-per-rev matches the config (or config updated to match).

### 4e. Tethered, wheels-off odometry check

Still up on the box. Command a slow `MOVE` (via the dashboard/controller) and
confirm the *reported* odometry matches the *commanded* motion:

- `MOVE 0.10 0.0` → both wheels forward, `odom_v ≈ +0.10`, `odom_omega ≈ 0`.
- `MOVE 0.0 0.5` → wheels counter-rotate, `odom_omega ≈ +0.5` (left/CCW),
  `odom_v ≈ 0`.
- `STOP` → both halt within the 1 s watchdog.
- Sign sanity: `+v` = forward, `+ω` = counter-clockwise/left (contract §1.1).

Then a **tape-measured ground-truth** figure (this is a study metric, spec §8):
later, on the floor with no dog, drive a fixed square and compare final pose to a
tape measure; the self-test's estimator hits ~2 cm / 0.05° in sim - real drift of
a few % is acceptable, gross error means re-check track width / counts-per-rev.

**Pass bar:** reported `odom_v`/`odom_omega` track commanded motion in magnitude
and sign; `STOP` halts within 1 s.

### 4f. First low-speed CBF test - NO DOG in the room

Wheels can go on the floor now, but **the room is empty of the dog.** This is the
fail-safe test battery from `SAFETY.md` Layer 3 / `SAFE_MOTION_CBF_SPEC §6`:

1. **Barrier holds the line:** place a target (a box) as a stand-in "dog", let
   vision+ToF range it, command a relentless forward `u_nom`. Confirm the robot
   settles at the comfort margin and the forward component clamps to `v ≤ 0` at
   the boundary - it never closes below `D_comfort`.
2. **Spoof `d_rd` below `D_comfort`:** confirm `u*` forward component clamps to
   ≤ 0.
3. **Pull the ToF:** confirm fallback to exposure-gate behavior (treat as
   at-the-boundary → no distance-reducing motion).
4. **Kill WiFi / `/motion` / `/telemetry`:** confirm the ESP32 STOPs on the TTL,
   and the Uno STOPs on its 1 s watchdog regardless.
5. **Power-cycle:** confirm it returns to exposure 0 (no motion).

**Pass bar:** every one of the five produces STOP or a non-distance-reducing
command. Any failure here is a stop-ship - fix before the dog is ever in the room.

### 4g. Graduated exposure ladder - with the dog, per the welfare rule

Only now, with every fail-safe proven in an empty room, does the dog enter. Ramp
exposure with the two-step arm→confirm commit (`m` proposes, `y` commits;
dashboard `/exposure/arm` + `/exposure/confirm`):

- **Level 0** (default) - no motion; buzzer/observe only. Baseline the dog's
  comfort with a motionless robot first.
- **Level 1** - orient/pivot only. Facing ≠ approaching; the CBF passes pivots
  untouched. Watch for flinch/arousal; the adaptive `D_comfort(s)` grows on
  arousal and only shrinks on the dog's *own* voluntary approach.
- **Level 2** - orient + gentle nudge, still barrier-filtered so the robot can
  **never** close below `D_comfort`. Distance is *earned by the dog*, never
  taken.

**Welfare rule (supreme):** at any flinch, freeze/retreat, or rising arousal,
drop a level and hold. Welfare precedence outranks the study schedule and every
metric. The robot never drives *at* the dog - every dog-directed motion is a
retreat (`CANIS_NEVER_APPROACH = 1`, compile-guarded on in mobile builds).

**Pass bar:** zero barrier violations (`h < 0`) - the headline claim (spec §8) - and no welfare escalation left unhandled.

---

## Step 5 - Headless Pi setup for YOLO (do once, can predate the motors)

The Pi runs headless (Raspberry Pi OS Lite 64-bit, Bookworm) with its fan, so a
nano model on CPU is real-time at our processed-frame cadence.

1. **OS + camera:** flash OS Lite, boot headless (SSH enabled). Enable the
   camera stack; for mode A/C's Pi camera confirm it enumerates.
2. **Deps + model** (exact commands, from `detect_yolo.py`'s header):
   ```
   sudo apt update
   sudo apt install -y python3-pip python3-opencv python3-picamera2
   pip3 install --break-system-packages ultralytics ncnn
   # one-time model download + NCNN export (run in ~/canis/vision):
   yolo export model=yolo11n.pt format=ncnn imgsz=320
   #   -> creates yolo11n_ncnn_model/  (the default model path)
   ```
   Keep `CANIS_YOLO_IMGSZ=320` matching the NCNN export.
3. **Verify without a camera or model:**
   ```
   python3 detect_yolo.py --selftest      # geometry + MJPEG parser + pipeline; PASS with no model
   python3 detect_fixed.py --selftest     # mode-C room geometry; PASS with no deps
   python3 detect_yolo.py --test --side left --zone hallway   # one fake POST to the dashboard
   ```
4. **Run live** (mode A example):
   ```
   python3 detect_yolo.py --zone kitchen --token "$CANIS_TOKEN"
   ```
5. **systemd note:** for an unattended run, wrap the detector (and, in mobile
   mode, `control/motion_controller.py`) in `systemd` user services with
   `Restart=on-failure` and the `CANIS_*` env vars (`CANIS_TOKEN`,
   `CANIS_CAMERA_MODE`, `CANIS_MOBILE_ENABLED`, `CANIS_DETECT_URL`) in the unit's
   `Environment=`. The vision guards (frozen-frame, blur, grace, reconnect)
   already self-heal a flaky camera, so the service just needs to come back after
   a reboot or a hard crash.

**Pass bar:** both `--selftest`s PASS with no model installed; the `--test` POST
lands on the dashboard; live run prints `posted zone=…`.

---

## Step 6 - Mode-specific extras

### Mode C (fixed room) - do this when going mobile

1. **Mount the fixed camera** overlooking the study room, lens
   `CANIS_FIXED_CAM_HEIGHT_M = 2.00` m above the floor, pitched **down**
   `CANIS_FIXED_CAM_TILT_DEG = 30°`. **Measure the real height and tilt** and
   update the `#define`s - they multiply straight into every distance the barrier
   trusts.
2. **Print + mount the robot marker:** a DICT_4X4_50 marker, id
   `CANIS_ROBOT_MARKER_ID = 17` (avoids the dog collar's default id 0), printed
   edge `CANIS_ROBOT_MARKER_SIZE_MM = 100` mm. Mount it **flat, facing UP** on the
   robot's top deck, its printed **top edge (corner0→corner1) toward the robot's
   FRONT** - that convention is how `detect_fixed.py` recovers heading. Marker
   plane height `CANIS_ROBOT_MARKER_HEIGHT_M = 0.18` m.
3. **Set HFOV** to your lens (`CANIS_FIXED_CAM_HFOV_DEG`: Pi Cam M3 = 66, v2 =
   62.2, wide M3 = 102).
4. **Roam caveat:** in fixed-room mode the robot is only perceived inside the
   camera's view. If `CANIS_ROAM_ENABLED` wanders it out of frame, localization
   is lost until it returns - fine for a single room, mind it if you enable roam.

Launch: `python3 detect_fixed.py --zone kitchen --cam-height 2.0 --tilt 30
--marker-id 17 --marker-height 0.18 --token …`.

### Mode B (esp32cam stream) - the long-term option only

1. **Buy + flash an AI-Thinker ESP32-CAM** with the stock `CameraWebServer`
   sketch; it serves MJPEG at `http://<ip>:81/stream`.
2. **Pin the URL:** give it a static DHCP lease or mDNS name so the URL survives
   reboots. Set `CANIS_ESP32CAM_STREAM_URL` (default
   `http://esp32cam.local:81/stream`) / env `CANIS_ESP32CAM_URL` / `--stream-url`.
3. **Power it properly:** the OV2640 browns out on a weak 5 V supply - give it a
   solid rail, separate from the ESP32 DevKit's.
4. **Honest FOV:** the stock OV2640 lens is ~65° HFOV - pass `--hfov 65` (or
   `CANIS_ESP32CAM_HFOV_DEG=65`) so bearing/distance stay honest.
5. **Safety stays onboard:** streaming is flaky by nature - keep the **safety
   range on the onboard ToF**; only identity/bearing ride the stream. A stalled
   stream (`CANIS_ESP32CAM_STALL_S = 5`) is treated exactly like a failed camera
   read (backoff + reconnect), so it degrades safe.

Launch: `CANIS_CAMERA_MODE=esp32cam_stream python3 detect_yolo.py
--stream-url http://<esp32cam>:81/stream --hfov 65 --zone kitchen --token …`.

---

## Bring-up order at a glance

```
1 mount motors (mirror-image, level)   ── wheels OFF
2 wire (motors→L298N, enc→ESP32, ToF/IMU→I²C, all @3V3)
3 flash ESP32 + Uno (flag still 0 - no behavior change)
4a set CANIS_MOBILE_ENABLED=1 (firmware + Pi env)
4b pick CANIS_CAMERA_MODE  (A now / C mobile / B long-term)
4c control --selftest  → 38/38 PASS ────────────── wheels OFF
4d verify encoder counts (by hand)  ───────────── wheels OFF
4e tethered odometry check (MOVE/STOP)  ───────── wheels OFF
4f low-speed CBF + fail-safe battery, NO DOG ──── wheels can touch, empty room
4g graduated exposure ladder WITH the dog ──────── welfare rule supreme
5 (any time) headless Pi + YOLO setup + selftests
6 mode-C camera+marker / mode-B stream extras
```

If any step fails, stop there. The dog never meets a system that hasn't passed
every step above it.
