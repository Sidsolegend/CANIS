# CANIS - Design decisions

The hardening changes made to the first working version of CANIS (the sketches
in `prototypes/` are that first version). Each change is cross-referenced to the
original `file:line` it addresses and tagged inline in the code as `[OPT-n]`.

| n | Theme | New / changed files |
|---|-------|---------------------|
| 1 | Single source of truth for tunables | `canis_config.h`, `dashboard/canis_config.py`, `CANIS_Core/CANIS_Core.ino` |
| 2 | Security (auth, validation, locking, prod server, hardening) | `dashboard/app.py`, `dashboard/canis-dashboard.service`, `dashboard/requirements.txt`, ESP32 token in `CANIS_Core.ino` |
| 3 | State hysteresis (Schmitt trigger) | `CANIS_Core/CANIS_Core.ino`, `dashboard/app.py` (sim mirror) |
| 4 | `dt` clamp in `updateArousal` | `CANIS_Core/CANIS_Core.ino` |
| 5 | Non-blocking WiFi reconnect watchdog | `CANIS_Core/CANIS_Core.ino` |
| 6 | Vision threading + reconnect + config | `vision/detect.py` |
| 7 | Dispense-ACK (count on delivery) | `CANIS_Core/CANIS_Core.ino`, `Treat_Dispenser_CANIS/Treat_Dispenser_CANIS.ino` |
| 8 | **[SAFETY]** Level-1 "orient only, never approach" invariant | `CANIS_Core/CANIS_Core.ino` (runStateBehavior) |
| 9 | Vision-latch TTL (no permanent phantom CORRECTION) | `CANIS_Core/CANIS_Core.ino`, `canis_config.h` |
| 10 | HTTP deferred while motion is active (watchdog-feed integrity) | `CANIS_Core/CANIS_Core.ino` |
| 11 | Dispenser jam detection (beam-break) + fault policy | `Treat_Dispenser_CANIS/…ino`, `CANIS_Core/…ino`, `canis_config.h` |
| 12 | Vision marker-loss grace period | `vision/vision_logic.py`, `vision/detect.py` |
| 13 | Frozen/stale-frame guard | `vision/vision_logic.py`, `vision/detect.py` |
| 14 | JSONL event log + study CSV export | `dashboard/event_log.py`, `dashboard/app.py`, `dashboard/templates/index.html`, `study/analyze_sessions.py` |

---

## 1. Single source of truth for tunables

**What changed.** All state-machine / sensor / movement / reward constants now
live once in `canis_config.h` (firmware) with a Python mirror in
`dashboard/canis_config.py` (dashboard). The optimized
`CANIS_Core.ino` `#include`s the header and refers to `CANIS_*` macros instead
of re-declaring locals; `app.py` reads `canis_config.py`.

**Why.** The same constants were hand-copied into three sketches and
re-typed a fourth time in the dashboard - and they had already drifted:

- `CANIS_Core/CANIS_Core.ino:81` - `CALM_REWARD_MS = 30000`
- `State_Machine_CANIS/State_Machine_CANIS.ino:23` - `CALM_REWARD_MS = 15000`
- `State_Machine_WiFi/State_Machine_WiFi.ino:38` - `CALM_REWARD_MS = 15000`
- `dashboard/app.py:29-33` - arousal/threshold constants re-typed by hand
  (and `CALM_REWARD_MS` not present at all)

The `15000` value is not just inconsistent, it is **wrong**: it is below
`REWARD_COOLDOWN_MS = 20000` (`CANIS_Core.ino:111`), so a "sustained calm"
reward could be requested inside the previous treat's cooldown. The canonical
value is `30000`, and `canis_config.h` adds a compile-time
`#error` guard (`CALM_REWARD_MS >= REWARD_COOLDOWN_MS`, plus hysteresis and
move-pulse guards) so a future bad edit fails the build instead of the dog.

**Better/safer.** One edit changes every consumer; impossible-to-satisfy values
break compilation loudly rather than silently misbehaving on hardware.

**Arduino build note.** The Arduino toolchain only sees headers *inside the
sketch folder*. So the canonical `canis_config.h` is **symlinked** in
as `CANIS_Core/canis_config.h -> ../canis_config.h`. Edit the
canonical file; the sketch tracks it through the link. (If your workflow can't
use symlinks, copy the file in and treat the copy as generated.)

**Recommended next step - kill drift at runtime, not just in source.** A `.h`
and a `.py` still can't share one literal file. The real fix is for the ESP32 to
**POST its live config once at boot** to a new `/config` endpoint, and for the
dashboard to display *those* values (thresholds, max, decay) instead of its own
copy. Then the numbers on the dashboard are provably the numbers the firmware is
running, and `canis_config.py` becomes only a fallback for the offline
simulator. Left as documented future work to avoid changing the wire protocol
in this pass.

---

## 2. Security (CRITICAL)

### 2a. Shared-secret token on mutating routes

**What changed.** `dashboard/app.py` requires a token on `/ingest`,
`/detect`, and `/command`. The token comes from the `CANIS_TOKEN` env var and is
accepted via the `X-CANIS-Token` header **or** a `?token=` query param; it is
compared with `hmac.compare_digest` (timing-safe). Missing/invalid → `401`.
`/` and `/api/state` stay public (read-only page + its data feed). If
`CANIS_TOKEN` is unset the app logs a loud warning and runs open (bench mode).
The ESP32 (`CANIS_Core.ino`) sends the same token: header on the `/ingest` POST,
query param on the `/command` GET (`SECRET_CANIS_TOKEN` from `arduino_secrets.h`,
defaulting to empty). The vision client sends `X-CANIS-Token` too.

**Why.** `dashboard/app.py:176-200` - `/ingest`, `/detect`, and
`/command` had **no authentication whatsoever**. Anyone on the LAN could POST
fake state to the dashboard, or - worse - POST to `/detect` to make the ESP32
believe the dog is in the zone and **drive the robot at a dog that's afraid of
moving objects**. That is the project's stated worst-case failure.

**Better/safer.** A network attacker without the secret can no longer inject
state or actuate the robot. Verified with the Flask test client: no-token and
bad-token requests return `401`; correct-token requests succeed.

### 2b. Input validation / bounds

**What changed.** `/ingest` validates `state` against the enum
(`CALM|AROUSAL|CORRECTION`) and clamps every numeric field: `arousal` to
`[0, AROUSAL_MAX]` (NaN-safe), `arousal_max` to a sane range, and the counters
to `[0, COUNT_MAX]`. `/detect` requires `zone` to be a printable string ≤
`ZONE_MAX_LEN` (32) chars - matching the ESP32's fixed `visionZone[24]` buffer.

**Why.** `dashboard/app.py:176-193` trusted the payload as-is: only
presence of `state` / `zone` was checked, values were cast without bounds. A
bogus giant `arousal`, a `NaN`, or an over-long zone string would flow straight
to the dashboard/robot. Verified: `arousal:9999 → 100.0`, `reward_count:-5 → 0`,
a 99-char zone → `400`.

### 2c. Thread locks around shared singletons

**What changed.** `LiveState`, `Simulator`, and `Command` each own a
`threading.Lock` guarding their read-modify-write sections
(`ingest`/`snapshot`/`set`/`current`).

**Why.** `dashboard/app.py` had no locking. Under any threaded WSGI
server (and this pass switches to a threaded one), concurrent `/ingest` +
`/api/state`, or two `/detect` posts, could interleave a read-modify-write and
publish a torn snapshot. Locks make each update atomic.

### 2d. Production server + systemd hardening

**What changed.** `dashboard/canis-dashboard.service` runs
**waitress** (`python3 -m waitress ... app:app`), loads `CANIS_TOKEN` from a
mode-600 `EnvironmentFile` (`/etc/canis/dashboard.env`), and adds a sandbox:
`NoNewPrivileges=yes`, `ProtectSystem=strict`, `PrivateTmp=yes`,
`ProtectHome=read-only`, `RestrictAddressFamilies`, `MemoryDenyWriteExecute`,
etc., with a tight `ReadWritePaths=/home/sidsolegend/dashboard`.
`requirements.txt` adds `waitress`. (waitress is pure-Python, so no compiler is
needed on the Pi; it's threaded, which is exactly why 2c was required.)

**Why.** `dashboard/canis-dashboard.service:19` ran
`ExecStart=/usr/bin/python3 .../app.py`, i.e. Flask's **development** server - explicitly not for network-facing use - with no privilege/filesystem
confinement. A compromised or crashed dev server had full user-level FS access.

### 2e. index.html

**No change needed.** The page only calls the public `GET /api/state`, which
requires no token, so `dashboard/templates/index.html` is an
unmodified copy kept so the optimized dashboard runs self-contained.

---

## 3. State hysteresis (Schmitt trigger)

**What changed.** The optimized state machine (`CANIS_Core.ino`, `classify()`)
uses **separate enter/exit thresholds**: enter `CORRECTION` at 70 but only leave
it below 60; enter `AROUSAL` at 20 but only fall to `CALM` below ~12
(`canis_config.h`: `CANIS_CORRECTION_ENTER/EXIT`, `CANIS_AROUSAL_ENTER/EXIT`).
The dashboard simulator's `_classify` mirrors this so demos behave like the
firmware.

**Why.** `CANIS_Core/CANIS_Core.ino:306-311` (and the identical
`State_Machine_*` copies) classified with a single threshold per boundary:

```
if      (arousal >= CORRECTION_ENTER) state = CORRECTION;
else if (arousal >= AROUSAL_ENTER)    state = AROUSAL;
else                                  state = CALM;
```

A dog hovering right at 70 would flip `AROUSAL↔CORRECTION` every ~20 ms tick,
re-firing `interventionCount++` and the buzzer cue on each entry, and bouncing
across 20 would spam the leaving-reward path.

**Better/safer.** The dead-band (60–70, 12–20) stops chatter: interventions and
rewards fire on genuine transitions, not on noise at a boundary. Welfare-
relevant, since each spurious `CORRECTION` is a buzzer/motion event at the dog.

---

## 4. `dt` clamp in `updateArousal`

**What changed.** `updateArousal` clamps `dt = min(dt, CANIS_DT_MAX_S)` (0.2 s)
before integrating.

**Why (original).** `CANIS_Core/CANIS_Core.ino:296-303` integrated raw `dt`. The
loop makes blocking calls (`pulseIn`, `HTTPClient` up to the 300 ms timeout,
WiFi work). If one loop stalls for, say, 3 s, `dt≈3`; with
`AROUSAL_RISE_PER_SEC=30` that single tick adds ~90 to arousal while a detection
is active - teleporting straight past `CORRECTION_ENTER` (70) and firing a
phantom intervention with no real escalation.

**Better/safer.** A stall now adds at most `(RISE−DECAY) × 0.2 = ~3.6` per tick,
so arousal can only climb at the intended rate. Interventions reflect sustained
presence, not scheduler hiccups.

---

## 5. Non-blocking WiFi reconnect watchdog

**What changed.** `loop()` calls `wifiWatchdog(now)`, which every
`CANIS_WIFI_CHECK_MS` (5 s) checks `WiFi.status()` and, if disconnected, calls
`WiFi.reconnect()` (returns immediately - no busy-wait).

**Why (original).** `CANIS_Core/CANIS_Core.ino:200-217` connected **once** in
`setup()`. `reportToPi()` and `pollCommand()` both early-return on
`WiFi.status() != WL_CONNECTED` (`:469`, `:501`), so any post-boot AP drop
disabled dashboard reporting *and* vision commands permanently until a manual
reboot.

**Better/safer.** The link self-heals after a router reboot or a range blip,
without ever blocking the 20 ms control loop.

---

## 6. Vision threading, reconnect, and config

**What changed (`vision/detect.py`).**

- **[OPT-6a] POST off the capture loop.** A daemon `Poster` thread drains a
  size-1 queue; the capture loop hands off the latest zone via `send()`
  (non-blocking, coalescing). Frame capture no longer waits on the network.
- **[OPT-6b] Camera-failure backoff + reconnect.** Consecutive failed reads
  back off exponentially (0.1 s → 5 s cap) and, after `READ_FAIL_MAX` (10),
  tear down and re-open the camera (`reopen()` on both backends).
- **[OPT-6c] Runtime config.** `--zone`/`CANIS_ZONE`,
  `--marker-ids`/`CANIS_MARKER_IDS`, `--directional`/`--no-directional`/
  `CANIS_DIRECTIONAL`, `--url`/`CANIS_DETECT_URL`, plus `--token`/`CANIS_TOKEN`.
  Precedence: CLI > env > default.

**Why.**

- `vision/detect.py:210-213` called `post_zone()` inline; with
  `urlopen(timeout=1)` a slow/unreachable dashboard stalled capture up to ~1 s
  per detection, dropping frames and delaying detection of the dog.
- `vision/detect.py:185-186` handled a failed grab with
  `print(...); time.sleep(0.1); continue` - an infinite tight loop that never
  attempts recovery if the camera drops off the bus.
- `vision/detect.py:42-50` hard-coded `ZONE`, `DOG_MARKER_IDS`, `DIRECTIONAL`
  as module constants, forcing a source edit per camera/room.

**Better/safer.** Capture stays real-time regardless of dashboard latency; a USB
re-enumeration or picamera2 hiccup self-recovers; and one code copy now serves
every room via flags/env (compiles clean; `--help` verified).

---

## 7. Dispense-ACK (count on delivery, not intent)

**What changed.** Guarded by `CANIS_USE_DISPENSE_ACK` (default on):

- Arduino (`Treat_Dispenser_CANIS/…ino`): after a drop **fully
  completes** (gate opened *and* closed), it emits `ACK\n` on the link's TX
  (pin 11).
- ESP32 (`CANIS_Core/…ino`): `reward()` sends `DISPENSE`, starts the
  cooldown, and marks the treat **pending** - it does **not** count it.
  `serviceAck()` watches `Serial2`; `reward_count` increments only on `ACK`.
  A missing ACK within `CANIS_ACK_TIMEOUT_MS` (1.5 s) is logged and the slot
  freed; the session cap counts the in-flight treat so the hopper can't
  overshoot while awaiting confirmation.

**Why (original).** `CANIS_Core/CANIS_Core.ino:445-449` did `rewardCount++;
Serial2.println("DISPENSE");` - it counted **intent**. A servo jam, empty
hopper, or a dropped serial byte still bumped the dashboard's "rewards given",
overstating what the dog actually received (a training-data integrity problem
for a behavior project).

**Better/safer.** The count now reflects treats **delivered**.

**Wiring caveat (documented, not silently assumed).** ACK needs the return line
the original left unwired: `Arduino pin 11 → ESP32 GPIO 4`. The ESP32 uses GPIO 4
(free in the design), so this does **not** cost the third ultrasonic. Direction
matters: Arduino 5 V TX into the ESP32's 3.3 V RX **must** go through a level
shifter or a 1 k/2 k resistor divider - do not wire 5 V straight to the ESP32.
If you can't add the return line, set `CANIS_USE_DISPENSE_ACK` to `0`; both
sketches still run and revert to count-on-send.

---

## 8. [SAFETY] Level-1 welfare invariant: orient only, NEVER approach

**What changed.** In `runStateBehavior()` (optimized `CANIS_Core.ino`, tagged
`[OPT-8][SAFETY]`), when `EXPOSURE_LEVEL < 2` a `FORWARD` bearing is rewritten
to a token in-place pivot (`LEFT`) before being sent to the Arduino.

**Why (original).** `CANIS_Core/CANIS_Core.ino:354-359`: at exposure level 1
("orient only: pivot to face the zone, never approach") the code did
`startMotion(dir, ORIENT_MS, now)` with `dir` from `localBearing()` /
`zoneToDir()`. Both can legitimately return `"FORWARD"` - the CENTRE ultrasonic
being nearest, or `zoneToDir`'s default - and the Arduino executes `FORWARD` as
a straight drive **at the dog**. That silently violated the project's central
welfare promise at the exact level where trust is being built.

**Better/safer.** Level 1 now cannot close distance on the dog under any
bearing. Unit-tested (`safe_orient_dir` in `tests/canis_logic.py`).

---

## 9. Vision-latch TTL (no permanent phantom CORRECTION)

**What changed.** `visionActive` now expires locally: `pollCommand()` stamps
`lastVisionRefresh` on every successful poll that sees the dog, and a new
`serviceVisionTtl()` clears the latch if no refresh arrives within
`CANIS_VISION_TTL_MS` (4 s; `#error`-guarded to exceed 2× the poll period).

**Why (original).** `CANIS_Core/CANIS_Core.ino:497-531`: the **only** code path
that could clear `visionActive` was a *successful* poll returning `NONE`. If
WiFi or the Pi died while the latch was set (the failure branch at `:527-529`
only sets a back-off), arousal was held up **forever**: a permanent phantom
`CORRECTION` - buzzer every 2 s and, at exposure ≥ 1, repeated orient motions - aimed at a dog that may have left long ago. The worst failure mode of the whole
system, reachable by a router reboot.

**Better/safer.** A dead Pi now degrades to "no vision input" within 4 s, the
same posture as never having had vision. Mirrored and tested as `VisionLatch`
in `tests/canis_logic.py`.

---

## 10. HTTP deferred while a motion is active

**What changed.** `reportToPi()` and `pollCommand()` return immediately while
`moveCmd` is active; the deferred report fires as soon as the motion ends.

**Why (original).** The loop is single-threaded. During an active motion the
ESP32 must re-send the move command every `MOVE_PULSE_MS` (500 ms) to feed the
Arduino's 1000 ms watchdog. But `reportToPi` + `pollCommand` can each block up
to `CANIS_HTTP_TIMEOUT_MS` (300 ms) - back-to-back, plus WiFi latency and
`readSensors`' ~50 ms of `pulseIn`, one loop iteration can exceed 500 ms and
push the watchdog-feed gap past 1000 ms mid-orient. Result: the Arduino
auto-stops and the next pulse restarts the motors - a visible stutter *at the
dog* during the most welfare-sensitive moment. (Fail direction was safe - halt,
not runaway - but a jerky, stop-start robot is exactly what frightens Shifu.)

**Better/safer.** Motions are short (≤ 700 ms orient / 400 ms approach), so
pausing telemetry during them costs ~1 report and guarantees smooth, continuous
motion pulses.

---

## 11. Dispenser jam detection (current-less: expected-beam-break timeout)

**What changed.**

- **Arduino** (`Treat_Dispenser_CANIS.ino`, `[OPT-11]`): an IR **beam-break
  pair across the chute exit** (one digital input, `A0`, `INPUT_PULLUP`;
  no current sensing, no shield). After the gate cycles, the sketch waits up to
  `DROP_TIMEOUT_MS` (600 ms) for the falling treat to interrupt the beam:
  seen → `ACK`; not seen → **one retry** with a longer gate hold
  (`RETRY_EXTRA_MS`); still nothing → reply **`JAM`**. Compile-gated behind
  `JAM_SENSOR_ENABLED` (default **0** = identical to the plain [OPT-7] sketch
  until the sensor is physically installed).
- **ESP32** (`CANIS_Core.ino`): `serviceAck()` now understands `JAM` - the
  treat is not counted, `jam_count` climbs (and is reported to the dashboard),
  and after `CANIS_JAM_FAULT_THRESHOLD` (2) **consecutive** jams the dispenser
  is marked FAULTED: further automatic rewards are suppressed, because a marker
  tone with no treat behind it actively erodes the conditioned signal. Manual
  `'d'` (after fixing the hopper) test-fires and clears the fault. An `ACK`
  resets the consecutive-jam streak. `CANIS_ACK_TIMEOUT_MS` was raised
  1500 → 2500 ms to cover the retry path (~1.8 s worst case).
- **Dashboard**: `jam_count` is validated on `/ingest`, logged as a
  `dispenser_jam` event, shown as a stat, and a red fault banner appears while
  `jam_count > 0`.

**Why (original).** `Treat_Dispenser_CANIS.ino:193-208`: `dispense()` had **no
idea whether a treat actually fell** - a wedged treat or empty hopper produced
a marker beep, an open-shut gate, and (pre-OPT-7) a counted reward. For a
conditioning system the treat *is* the payload; silently not delivering it is a
data-integrity **and** a training-integrity failure. [OPT-7]'s ACK confirmed
the *gate cycled*, not that a *treat fell* - this closes that gap with actual
physical verification.

**Better/safer.** Delivery is now verified by a physical sensor; a fault makes
the system go quiet instead of beeping at the dog with an empty hand. Policy
mirrored and tested as `DispenserHealth` in `tests/canis_logic.py`.

---

## 12. Vision marker-loss grace period

**What changed.** `vision/vision_logic.py` adds `DetectionGrace` (pure logic,
unit-tested): a real sighting reports its zone; after the marker vanishes the
last zone is **held** for `CANIS_VISION_GRACE_S` (default 1.5 s), then clears.
`detect.py` now feeds every processed frame through it, so brief marker losses
no longer stop the 1 Hz posting.

**Why (original/optimized-v1).** ArUco loses a collar marker for a few frames
whenever the dog turns his head or the marker tilts past ~60°. Detection then
flapped: the dashboard's `COMMAND_TTL` (3 s) expired and re-armed, the ESP32's
`visionActive` toggled, and arousal saw-toothed across a threshold - potentially firing a "reward for leaving" at a dog that merely looked away
(rewarding the wrong behavior corrupts the training) or re-triggering
interventions.

**Trade-off (deliberate, documented).** A genuine departure now clears up to
1.5 s later, delaying the leaving-reward by the same amount. Kept small
relative to the ~5 s arousal decay; tune with the `CANIS_VISION_GRACE_S` env.

---

## 13. Frozen/stale-frame guard

**What changed.** `vision_logic.FrozenFrameGuard`: every frame is
fingerprinted (a cheap `[::32, ::32]` subsample); `FROZEN_MAX` (30) identical
fingerprints in a row ⇒ the stream is frozen ⇒ `detect.py` reopens the camera.

**Why.** A wedged camera driver (seen with UVC webcams, occasionally
picamera2) can keep returning the **same buffer** forever. Reads "succeed", so
the [OPT-6b] read-*failure* reconnect never fires - the system believes it has
live vision while actually watching a frozen photo of an empty room. Silent
sensor loss in a safety-relevant perception path must be detected, not assumed
away. Live sensors always carry noise, so identical fingerprints essentially
never occur on real video (unit-tested).

---

## 14. JSONL event log + longitudinal-study export

**What changed.**

- `dashboard/event_log.py` (new): append-only **JSONL event log** - schema
  documented in the module docstring - recording `session_start`,
  `state_transition`, `treat_dispensed`, `dispenser_jam`, `zone_seen`,
  `exposure_change`, `firmware_reboot`, and `link_gap` events, each stamped
  with the Pi's wall clock and a session id. Crash-safe (open/append/close per
  write; the reader tolerates a torn final line).
- `dashboard/app.py` (`[OPT-14]`): derives those events from `/ingest` deltas
  and `/detect` posts; adds `GET /export/events.csv`, `GET /export/sessions.csv`
  (per-session aggregates: state occupancy, corrections, treats, jams, zone
  episodes/time, first-zone latency, link gaps, reboots, exposure levels), and
  `GET /api/events`. Log dir: `CANIS_LOG_DIR` (default `dashboard/logs/`,
  inside the systemd unit's `ReadWritePaths`).
- `study/analyze_sessions.py` (new): offline CLI over the same
  `summarize_sessions()` implementation (one aggregator, two entry points - the
  export and the analyzer can never disagree). `--generate-example` writes a
  **clearly-marked SYNTHETIC** log for pipeline testing.
- The ESP32 now also reports `jam_count` and `exposure_level` in its 1 Hz POST
  so the log captures the welfare knob's position over time.

**Why (original).** The dashboard kept **only the latest snapshot**
(`app.py:41-75`): the behavioral record evaporated on every refresh. The
project's stated scientific artifact is a *longitudinal* fear-response record - impossible to produce without durable, timestamped session data. This is the
instrumentation that `study/STUDY_PROTOCOL.md` runs on.

---

## [C7] Commissioning / bring-up tooling (persisted calibration + guided self-test)

**What.** The two bring-up steps that used to mean *physically redoing the
assembly*, plus the 7-step manual bench doc, are now one-time software actions the
builder runs once - right after assembly - and the robot stays correctly
configured from then on.

- **Motor direction is a saved flag, not a lead-swap.** `Treat_Dispenser_CANIS.ino`
  gains persisted `MOTOR_L_INVERT` / `MOTOR_R_INVERT` booleans (EEPROM) that
  `setMotor()` XORs into the direction sign. A wheel that spins backward is fixed
  with `INVL` / `INVR` (toggle + save) instead of swapping that motor's two OUT
  leads (was `hardware/TESTING.md:28`).
- **Dispenser flap zero is a saved trim, not a horn re-press.** A persisted
  `TRIM_OFFSET` (±15°, clamped) shifts `CLOSED_ANGLE`/`OPEN_ANGLE` wherever the
  servo is driven; `TRIM+` / `TRIM-` nudge + save it (was
  `hardware/BUILD_GUIDE_v2.md:83`). `CAL?` prints the live calibration.
- These live on the **Arduino Uno** because that is where `setMotor()` and the
  servo run; the Uno's persistence primitive is `EEPROM` (`Preferences` is
  ESP32-only). The ESP32 drives them remotely over the existing link.

**Guided self-test - ESP32 key `c`.** `CANIS_Core.ino` gains one re-runnable,
entirely serial-driven routine that sequences the old TESTING.md bench checks:
1. **Motors** - pulses each wheel (`MOTL`/`MOTR`), asks "spin the expected way?
   [y/n]"; on `n` it flips + persists that motor's invert flag on the Uno
   (`INVL`/`INVR`) and re-pulses to verify.
2. **Dispenser** - cycles the flap, asks if it looked right; on `n` offers
   `+`/`-` trim nudges (`TRIM+`/`TRIM-`) that re-fire so the fix is visible.
3. **Sensors** - prints all three ultrasonic distances + PIR with PASS/FAIL vs
   sane thresholds, using the confirmed-read pattern absorbed from
   `Sensors_Test.ino`. It reads the PIR pin in the **`INPUT_PULLDOWN`** mode
   `setup()` already set - *not* a re-declared `INPUT` - which fixes the
   test-vs-firmware pin-mode divergence so the bench test can't
   show phantom motion the real brain never would.

**Vision - `python3 detect.py --selftest`.** Opens the camera, grabs a handful of
frames within a short timeout, PASS/FAIL on ArUco detection, and prints concrete
remediation (cable/backend, focus, lighting, marker distance, wrong marker IDs).
Runs standalone with just a camera - no dashboard or other CANIS service needed.
Replaces the manual camera step in `hardware/TESTING.md` Test 5.

**Coverage.** The invert XOR and trim-clamp/angle logic are mirrored as pure
functions in `tests/canis_logic.py` and unit-tested in `tests/test_calibration.py`
(hardware-free).

---

## SECURITY note (credentials)

- **Rotate the WiFi password.** The real `arduino_secrets.h` (with the live
  SSID/password) sat in plaintext on disk in `CANIS_Core/` and
  `State_Machine_WiFi/`. Treat that password as exposed and **rotate it on the
  router**, then update the (gitignored) secrets file. No secret was read,
  copied, or reproduced in this optimization pass.
- **Scrub the SSID from the WiFi test sketches.** `WiFi_Connect_Test/` and
  `WiFi_Scan_Test/` reference WiFi config directly rather than going through
  `arduino_secrets.h`; move any network name/password out of that source and
  behind the gitignored secrets header (or delete the hard-coded values) so the
  SSID isn't committed.
- **Set a dashboard token.** Generate `CANIS_TOKEN`
  (`openssl rand -hex 16`), put it in the mode-600 `/etc/canis/dashboard.env`
  for the service, and mirror it into `SECRET_CANIS_TOKEN` in the ESP32's
  `arduino_secrets.h`. Without it the dashboard runs unauthenticated (a warning
  is logged) - acceptable only on an isolated bench.
- Keep `arduino_secrets.h` gitignored (the template
  `arduino_secrets.h.example` documents the required `SECRET_*` names).
