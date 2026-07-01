# CANIS — Optimizations

Optimized **duplicates** of the CANIS sources. Nothing in the original tree was
modified; every file here is new and lives under `optimized/`. Each change is
cross-referenced to the original `file:line` it addresses and tagged inline in
the code as `[OPT-n]`.

| n | Theme | New / changed files |
|---|-------|---------------------|
| 1 | Single source of truth for tunables | `canis_config.h`, `dashboard/canis_config.py`, `CANIS_Core/CANIS_Core.ino` |
| 2 | Security (auth, validation, locking, prod server, hardening) | `dashboard/app.py`, `dashboard/canis-dashboard.service`, `dashboard/requirements.txt`, ESP32 token in `CANIS_Core.ino` |
| 3 | State hysteresis (Schmitt trigger) | `CANIS_Core/CANIS_Core.ino`, `dashboard/app.py` (sim mirror) |
| 4 | `dt` clamp in `updateArousal` | `CANIS_Core/CANIS_Core.ino` |
| 5 | Non-blocking WiFi reconnect watchdog | `CANIS_Core/CANIS_Core.ino` |
| 6 | Vision threading + reconnect + config | `vision/detect.py` |
| 7 | Dispense-ACK (count on delivery) | `CANIS_Core/CANIS_Core.ino`, `Treat_Dispenser_CANIS/Treat_Dispenser_CANIS.ino` |

---

## 1. Single source of truth for tunables

**What changed.** All state-machine / sensor / movement / reward constants now
live once in `optimized/canis_config.h` (firmware) with a Python mirror in
`optimized/dashboard/canis_config.py` (dashboard). The optimized
`CANIS_Core.ino` `#include`s the header and refers to `CANIS_*` macros instead
of re-declaring locals; `app.py` reads `canis_config.py`.

**Why (originals).** The same constants were hand-copied into three sketches and
re-typed a fourth time in the dashboard — and they had already drifted:

- `CANIS_Core/CANIS_Core.ino:81` — `CALM_REWARD_MS = 30000`
- `State_Machine_CANIS/State_Machine_CANIS.ino:23` — `CALM_REWARD_MS = 15000`
- `State_Machine_WiFi/State_Machine_WiFi.ino:38` — `CALM_REWARD_MS = 15000`
- `dashboard/app.py:29-33` — arousal/threshold constants re-typed by hand
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
sketch folder*. So the canonical `optimized/canis_config.h` is **symlinked** in
as `optimized/CANIS_Core/canis_config.h -> ../canis_config.h`. Edit the
canonical file; the sketch tracks it through the link. (If your workflow can't
use symlinks, copy the file in and treat the copy as generated.)

**Recommended next step — kill drift at runtime, not just in source.** A `.h`
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

**What changed.** `optimized/dashboard/app.py` requires a token on `/ingest`,
`/detect`, and `/command`. The token comes from the `CANIS_TOKEN` env var and is
accepted via the `X-CANIS-Token` header **or** a `?token=` query param; it is
compared with `hmac.compare_digest` (timing-safe). Missing/invalid → `401`.
`/` and `/api/state` stay public (read-only page + its data feed). If
`CANIS_TOKEN` is unset the app logs a loud warning and runs open (bench mode).
The ESP32 (`CANIS_Core.ino`) sends the same token: header on the `/ingest` POST,
query param on the `/command` GET (`SECRET_CANIS_TOKEN` from `arduino_secrets.h`,
defaulting to empty). The vision client sends `X-CANIS-Token` too.

**Why (originals).** `dashboard/app.py:176-200` — `/ingest`, `/detect`, and
`/command` had **no authentication whatsoever**. Anyone on the LAN could POST
fake state to the dashboard, or — worse — POST to `/detect` to make the ESP32
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
`ZONE_MAX_LEN` (32) chars — matching the ESP32's fixed `visionZone[24]` buffer.

**Why (originals).** `dashboard/app.py:176-193` trusted the payload as-is: only
presence of `state` / `zone` was checked, values were cast without bounds. A
bogus giant `arousal`, a `NaN`, or an over-long zone string would flow straight
to the dashboard/robot. Verified: `arousal:9999 → 100.0`, `reward_count:-5 → 0`,
a 99-char zone → `400`.

### 2c. Thread locks around shared singletons

**What changed.** `LiveState`, `Simulator`, and `Command` each own a
`threading.Lock` guarding their read-modify-write sections
(`ingest`/`snapshot`/`set`/`current`).

**Why (originals).** `dashboard/app.py` had no locking. Under any threaded WSGI
server (and this pass switches to a threaded one), concurrent `/ingest` +
`/api/state`, or two `/detect` posts, could interleave a read-modify-write and
publish a torn snapshot. Locks make each update atomic.

### 2d. Production server + systemd hardening

**What changed.** `optimized/dashboard/canis-dashboard.service` runs
**waitress** (`python3 -m waitress ... app:app`), loads `CANIS_TOKEN` from a
mode-600 `EnvironmentFile` (`/etc/canis/dashboard.env`), and adds a sandbox:
`NoNewPrivileges=yes`, `ProtectSystem=strict`, `PrivateTmp=yes`,
`ProtectHome=read-only`, `RestrictAddressFamilies`, `MemoryDenyWriteExecute`,
etc., with a tight `ReadWritePaths=/home/sidsolegend/dashboard`.
`requirements.txt` adds `waitress`. (waitress is pure-Python, so no compiler is
needed on the Pi; it's threaded, which is exactly why 2c was required.)

**Why (originals).** `dashboard/canis-dashboard.service:19` ran
`ExecStart=/usr/bin/python3 .../app.py`, i.e. Flask's **development** server —
explicitly not for network-facing use — with no privilege/filesystem
confinement. A compromised or crashed dev server had full user-level FS access.

### 2e. index.html

**No change needed.** The page only calls the public `GET /api/state`, which
requires no token, so `optimized/dashboard/templates/index.html` is an
unmodified copy kept so the optimized dashboard runs self-contained.

---

## 3. State hysteresis (Schmitt trigger)

**What changed.** The optimized state machine (`CANIS_Core.ino`, `classify()`)
uses **separate enter/exit thresholds**: enter `CORRECTION` at 70 but only leave
it below 60; enter `AROUSAL` at 20 but only fall to `CALM` below ~12
(`canis_config.h`: `CANIS_CORRECTION_ENTER/EXIT`, `CANIS_AROUSAL_ENTER/EXIT`).
The dashboard simulator's `_classify` mirrors this so demos behave like the
firmware.

**Why (originals).** `CANIS_Core/CANIS_Core.ino:306-311` (and the identical
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
is active — teleporting straight past `CORRECTION_ENTER` (70) and firing a
phantom intervention with no real escalation.

**Better/safer.** A stall now adds at most `(RISE−DECAY) × 0.2 = ~3.6` per tick,
so arousal can only climb at the intended rate. Interventions reflect sustained
presence, not scheduler hiccups.

---

## 5. Non-blocking WiFi reconnect watchdog

**What changed.** `loop()` calls `wifiWatchdog(now)`, which every
`CANIS_WIFI_CHECK_MS` (5 s) checks `WiFi.status()` and, if disconnected, calls
`WiFi.reconnect()` (returns immediately — no busy-wait).

**Why (original).** `CANIS_Core/CANIS_Core.ino:200-217` connected **once** in
`setup()`. `reportToPi()` and `pollCommand()` both early-return on
`WiFi.status() != WL_CONNECTED` (`:469`, `:501`), so any post-boot AP drop
disabled dashboard reporting *and* vision commands permanently until a manual
reboot.

**Better/safer.** The link self-heals after a router reboot or a range blip,
without ever blocking the 20 ms control loop.

---

## 6. Vision threading, reconnect, and config

**What changed (`optimized/vision/detect.py`).**

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

**Why (originals).**

- `vision/detect.py:210-213` called `post_zone()` inline; with
  `urlopen(timeout=1)` a slow/unreachable dashboard stalled capture up to ~1 s
  per detection, dropping frames and delaying detection of the dog.
- `vision/detect.py:185-186` handled a failed grab with
  `print(...); time.sleep(0.1); continue` — an infinite tight loop that never
  attempts recovery if the camera drops off the bus.
- `vision/detect.py:42-50` hard-coded `ZONE`, `DOG_MARKER_IDS`, `DIRECTIONAL`
  as module constants, forcing a source edit per camera/room.

**Better/safer.** Capture stays real-time regardless of dashboard latency; a USB
re-enumeration or picamera2 hiccup self-recovers; and one code copy now serves
every room via flags/env (compiles clean; `--help` verified).

---

## 7. Dispense-ACK (count on delivery, not intent)

**What changed.** Guarded by `CANIS_USE_DISPENSE_ACK` (default on):

- Arduino (`optimized/Treat_Dispenser_CANIS/…ino`): after a drop **fully
  completes** (gate opened *and* closed), it emits `ACK\n` on the link's TX
  (pin 11).
- ESP32 (`optimized/CANIS_Core/…ino`): `reward()` sends `DISPENSE`, starts the
  cooldown, and marks the treat **pending** — it does **not** count it.
  `serviceAck()` watches `Serial2`; `reward_count` increments only on `ACK`.
  A missing ACK within `CANIS_ACK_TIMEOUT_MS` (1.5 s) is logged and the slot
  freed; the session cap counts the in-flight treat so the hopper can't
  overshoot while awaiting confirmation.

**Why (original).** `CANIS_Core/CANIS_Core.ino:445-449` did `rewardCount++;
Serial2.println("DISPENSE");` — it counted **intent**. A servo jam, empty
hopper, or a dropped serial byte still bumped the dashboard's "rewards given",
overstating what the dog actually received (a training-data integrity problem
for a behavior project).

**Better/safer.** The count now reflects treats **delivered**.

**Wiring caveat (documented, not silently assumed).** ACK needs the return line
the original left unwired: `Arduino pin 11 → ESP32 GPIO 4`. The ESP32 uses GPIO 4
(free in the design), so this does **not** cost the third ultrasonic. Direction
matters: Arduino 5 V TX into the ESP32's 3.3 V RX **must** go through a level
shifter or a 1 k/2 k resistor divider — do not wire 5 V straight to the ESP32.
If you can't add the return line, set `CANIS_USE_DISPENSE_ACK` to `0`; both
sketches still run and revert to count-on-send.

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
  is logged) — acceptable only on an isolated bench.
- Keep `arduino_secrets.h` gitignored (the template
  `arduino_secrets.h.example` documents the required `SECRET_*` names).
