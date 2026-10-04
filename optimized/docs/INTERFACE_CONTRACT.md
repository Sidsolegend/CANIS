# CANIS — Interface Contract (mobile drivetrain + YOLO upgrade)

**Status: LAW.** This document locks the shared wire formats for the parallel
implementation tracks (firmware, Uno, vision, dashboard). If your code and
this file disagree, your code is wrong. Change requests go through this file
FIRST, then fan out.

Companion documents (read, do not duplicate):
- `docs/SAFE_MOTION_CBF_SPEC.md` — WHY the barrier layer exists and its math.
  This file specifies only the MESSAGES that layer rides on.
- `docs/SAFETY.md` — the fail-safe stack. Nothing here removes a layer.
- `canis_config.h` (repo root, symlinked into `CANIS_Core/`) — single source
  of truth for every constant named here. The new block is
  `Mobile drivetrain + CBF safe motion`.

New config constants this contract keys off (exact names):

| Constant | Default | Meaning |
|---|---|---|
| `CANIS_MOBILE_ENABLED` | `0` | 0 = stationary (SAFE default), 1 = CBF differential drive |
| `CANIS_ENC_L_A_PIN` / `CANIS_ENC_L_B_PIN` | `18` / `19` | left quadrature encoder (ESP32 PCNT) |
| `CANIS_ENC_R_A_PIN` / `CANIS_ENC_R_B_PIN` | `16` / `17` | right quadrature encoder (ESP32 PCNT) |
| `CANIS_ENC_COUNTS_PER_REV` | `1320` | counts per wheel rev, 4x quadrature decode |
| `CANIS_WHEEL_DIAMETER_MM` | `65.0f` | drive wheel diameter |
| `CANIS_WHEEL_BASE_MM` | `140.0f` | track width (wheel-center to wheel-center) |
| `CANIS_D_MIN_CM` / `CANIS_D_MAX_CM` | `60` / `200` | comfort-margin band for D_comfort(s) |
| `CANIS_CBF_GAMMA` | `0.5f` | barrier aggressiveness (small = conservative) |
| `CANIS_CONTROL_RATE_HZ` | `20` | ESP32 wheel-velocity PI loop |
| `CANIS_ODOM_STREAM_HZ` | `10` | /telemetry stream + /motion poll rate |
| `CANIS_MOTION_CMD_TTL_MS` | `600UL` | (v, omega) freshness; stale → ESP32 STOPs |
| `CANIS_MAX_LINEAR_MPS` | `0.35f` | hard \|v\| clamp, firmware-side |
| `CANIS_MAX_ANGULAR_RPS` | `1.5f` | hard \|omega\| clamp, firmware-side |

---

## 1. ESP32 → Pi telemetry (EXTENDED)

### 1.1 `/ingest` — existing 1 Hz state report, extended

`POST http://<PI_HOST>:5000/ingest`, `Content-Type: application/json`,
header `X-CANIS-Token`. Cadence unchanged: `CANIS_REPORT_MS` = 1 Hz plus
on-change. **Every existing field keeps its exact name, type, and semantics.**

```json
{
  "state": "CALM|AROUSAL|CORRECTION",
  "arousal": 42.5,
  "arousal_max": 100.0,
  "intervention_count": 3,
  "reward_count": 5,
  "jam_count": 0,
  "exposure_level": 0,
  "uptime_sec": 1234,

  "mobile": 1,
  "enc_l": 152340,
  "enc_r": 151998,
  "odom_v": 0.212,
  "odom_omega": -0.087,
  "tof_m": 1.42,
  "heading_deg": 214.6
}
```

New fields (exact names — these are the contract):

| Field | Type | Units / range | Meaning |
|---|---|---|---|
| `mobile` | int | `0` or `1` | live value of `CANIS_MOBILE_ENABLED` |
| `enc_l` | int (64-bit safe) | counts | cumulative SIGNED left-encoder count since boot (never reset mid-session) |
| `enc_r` | int (64-bit safe) | counts | cumulative SIGNED right-encoder count since boot |
| `odom_v` | float, 3 dp | m/s | body linear velocity from encoder odometry (+ = forward) |
| `odom_omega` | float, 3 dp | rad/s | body angular velocity (+ = counter-clockwise / left turn) |
| `tof_m` | float, 3 dp | meters; `-1.0` = no valid return | VL53LX robot→dog range. `-1.0` is the ONLY no-data sentinel; never send 0.0 for "unknown" |
| `heading_deg` | float, 1 dp | `[0.0, 360.0)` | IMU yaw heading, clockwise-positive from boot reference |

Rules:
- When `CANIS_MOBILE_ENABLED = 0` the firmware **omits all seven new fields**
  — the body is byte-for-byte the legacy shape. The dashboard MUST accept both
  shapes and MUST ignore unknown keys (forward compatibility); it 400s only on
  the existing validations (bad `state` enum, unbounded numerics).
- Absent field ⇒ dashboard stores `null`, never a fabricated 0.

### 1.2 `/telemetry` — NEW high-rate motion frame (mobile only)

The 1 Hz `/ingest` is too slow for the Pi's estimator (spec §2 requires
≥ 10 Hz). When `CANIS_MOBILE_ENABLED = 1` the ESP32 additionally streams:

`POST http://<PI_HOST>:5000/telemetry`, `application/json`, `X-CANIS-Token`,
at `CANIS_ODOM_STREAM_HZ` (10 Hz):

```json
{"ts_ms": 8123456, "enc_l": 152340, "enc_r": 151998,
 "odom_v": 0.212, "odom_omega": -0.087, "tof_m": 1.42, "heading_deg": 214.6}
```

- `ts_ms` = ESP32 `millis()` at sample time (uint32, wraps ~49 days; the Pi
  handles wrap). All other fields identical in name/type/units to §1.1.
- **OPT-10 exemption:** the "defer HTTP while motion is active" rule does NOT
  apply to `/telemetry` (a moving robot is exactly when the estimator needs
  data). It still applies to `/ingest`. `/telemetry` uses the same
  `CANIS_HTTP_TIMEOUT_MS` = 300 ms budget; a missed frame is dropped, never
  queued (freshest-only, like the vision Poster).
- Dashboard: token-required, feeds the estimator, is NOT logged per-frame to
  the event log (10 Hz would flood JSONL). If the ESP32 receives 404/401 from
  `/telemetry` (old dashboard), it MUST treat mobile as unavailable: STOP and
  fall back to stationary behavior.

---

## 2. Pi → ESP32 → Arduino motion command (differential drive)

### 2.1 Pi → ESP32: `GET /motion` (NEW, mirrors the `/command` pattern)

`GET http://<PI_HOST>:5000/motion?token=<token>` → `text/plain` (NOT JSON),
polled by the ESP32 at `CANIS_ODOM_STREAM_HZ` (10 Hz) **only when
`CANIS_MOBILE_ENABLED = 1`**. Response body is exactly one line:

```
MOVE <v> <omega>        e.g.  MOVE 0.20 -0.60
STOP
```

- `<v>` in m/s, `<omega>` in rad/s, ≤ 3 decimal places, ASCII, space-separated.
  Sign conventions as §1.1 (`+v` forward, `+omega` CCW).
- `STOP` is returned whenever there is no fresh, valid command: the CBF
  controller hasn't updated within `CANIS_MOTION_CMD_TTL_MS`, mobile mode is
  off Pi-side, or the QP output is a hold. Bad token → `UNAUTHORIZED` 401
  (same as `/command`).
- The values served are the **post-QP `u*`** (already barrier-filtered,
  spec §4). The dashboard never serves the raw `u_nom`.

### 2.2 ESP32-side handling (LAW)

1. Clamp: `|v| ≤ CANIS_MAX_LINEAR_MPS`, `|omega| ≤ CANIS_MAX_ANGULAR_RPS` —
   always, regardless of what the Pi sent (defense in depth).
2. Hard invariant clamp: if the dog is currently detected AND
   `tof_m ≤ CANIS_D_MIN_CM / 100.0`, the forward component is clamped to
   `v ≤ 0` in firmware. `CANIS_NEVER_APPROACH` remains supreme; the CBF layer
   only ever ADDS conservatism (a compile guard enforces this pairing).
3. Freshness: if the last successful `MOVE` poll is older than
   `CANIS_MOTION_CMD_TTL_MS` (600 ms), the ESP32 commands STOP on its own.
4. Wheel mapping (track `L = CANIS_WHEEL_BASE_MM / 1000.0` m):

   ```
   v_L = v − omega·L/2        v_R = v + omega·L/2
   ```

   The ESP32 closes a per-wheel velocity PI loop at `CANIS_CONTROL_RATE_HZ`
   (20 Hz) using the encoders, producing signed PWM duty per side.

### 2.3 ESP32 → Uno: new `VEL` verb on the EXISTING Serial2 link

Same link, unchanged: Serial2 @ 9600 8N1, ESP32 GPIO13 → Uno pin 10,
newline-terminated ASCII words. New grammar (appended, nothing removed):

```
VEL <pwmL> <pwmR>\n       pwmL, pwmR ∈ [-255, 255], signed integers
```

- Positive = that wheel forward. The Uno maps sign → IN1..IN4 direction pins
  and magnitude → ENA/ENB PWM. It does NO kinematics — it stays the dumb,
  certifiable executor (spec §7).
- **Re-send cadence: every `CANIS_MOVE_PULSE_MS` (500 ms) while in motion**,
  exactly like the legacy `FORWARD/…` words, because:
- **The Uno's `MOTION_TIMEOUT_MS` = 1000 ms watchdog is UNCHANGED and applies
  to `VEL`.** Any `VEL`-driven motion auto-STOPs 1 s after the last command,
  independent of the ESP32 and the Pi. No implementer may lengthen, bypass, or
  special-case this watchdog. Watchdog chain, fastest-first:

  | Layer | Trigger | Action |
  |---|---|---|
  | Pi/dashboard | controller stale > `CANIS_MOTION_CMD_TTL_MS` | serves `STOP` |
  | ESP32 | `/motion` poll stale > 600 ms | sends `STOP` |
  | Uno | no motion word for 1000 ms | cuts motor PWM itself |

- Stops MUST be sent as the explicit word `STOP` (existing verb), not
  `VEL 0 0`, so old and new firmware behave identically on a stop.
- Legacy verbs (`FORWARD/BACKWARD/LEFT/RIGHT/STOP`, `DISPENSE <id>`, C7 calib)
  are untouched. An old Uno sketch that doesn't know `VEL` ignores the unknown
  word → no motion → fails SAFE. `ACK <id>` / `JAM <id>` replies unchanged.

---

## 3. Detection contract: YOLO replaces ArUco (backward-compatible)

`POST http://127.0.0.1:5000/detect` (env `CANIS_DETECT_URL`),
`application/json`, `X-CANIS-Token`. **The existing required field is
unchanged and remains sufficient by itself** — the current ArUco `detect.py`
stays a valid reporter forever.

```json
{
  "zone": "kitchen-left",

  "dog_present": true,
  "bearing_deg": -18.5,
  "distance_m": 1.4,
  "confidence": 0.87,
  "node_id": "cam-kitchen",
  "ts": 1783190522.412
}
```

| Field | Req? | Type | Meaning |
|---|---|---|---|
| `zone` | **required** | str, printable, ≤ 32 chars (`ZONE_MAX_LEN`) | same semantics as today: `"{zone}-{side}"` when directional, else `"{zone}"`. This is what `/command` serves and what the ESP32's `zoneToDir()` parses — DO NOT change its shape |
| `dog_present` | optional | bool | detector's verdict. Present ⇒ MUST be `true` (see POST rule below) |
| `bearing_deg` | optional | float, 1 dp | dog bearing, `0.0` = straight ahead, negative = left, positive = right. Modes A/B (`detect_yolo.py`): **camera frame**, range ± half the camera HFOV. Mode C (`detect_fixed.py`): **robot frame** (camera == robot frame only in A/B), range up to ±180°. Consumers use only the **sign** for L/R side-mapping, so the frame difference is transparent to `zoneToDir()`. |
| `distance_m` | optional | float or `null` | estimated robot→dog range (ToF gated by bearing, or bbox-height heuristic). `null` = unknown; never fake a number |
| `confidence` | optional | float `[0.0, 1.0]` | detector confidence for the reported dog |
| `node_id` | optional | str, printable, ≤ 32 chars | which reporter this is (multi-camera/zone). Omitted ⇒ dashboard treats as `"default"` |
| `ts` | optional | float | reporter's epoch seconds at frame capture |

Rules (LAW):
- **POST only when a dog is being reported** — i.e. `dog_present` is true or
  the detection is inside the reuse of `DetectionGrace` (1.5 s hold).
  Silence still means "no dog"; never POST a `dog_present: false` body.
- **Side mapping** (so the ESP32 zone grammar is stable across detectors):
  `side = "left"` if `bearing_deg < -10.0`, `"right"` if `> +10.0`, else
  `"center"`. ArUco's thirds-of-frame rule and YOLO's bearing rule MUST land
  on the same label for the same dog position.
- **Confidence gating is node-side:** a YOLO node only POSTs detections with
  `confidence ≥ 0.60` (node config `YOLO_CONF_MIN`, tune per camera). The
  dashboard does not re-gate.
- **Multi-reporter arbitration:** unchanged from today — most recent valid
  POST wins, valid for `COMMAND_TTL` = 3.0 s. `node_id` disambiguates in logs
  only; it does not create per-node channels. Nodes therefore SHOULD use
  distinct zone base names (their physical location) so last-writer-wins is
  meaningful.
- **Dashboard behavior:** validates `zone` exactly as today (400 on bad zone);
  accepts and stores the optional fields when present, ignores unknown keys;
  logs them on the existing `zone_seen` event. `/command` (ESP32-facing)
  remains a bare text/plain zone label — the new fields ride the event log and
  feed the Pi-side comfort estimator (`distance_m`, `confidence`), they do NOT
  change what the ESP32 polls.
- Vision-side guards (`DetectionGrace`, `FrozenFrameGuard`,
  `BlurSkipMonitor`) and the Poster (size-1 coalescing queue, 1 Hz max) are
  reused as-is around the YOLO inference.

### 3.1 `/config` announce — new whitelisted keys

The ESP32's once-per-link `POST /config` gains these keys (exact names, all
numeric, appended to the dashboard's `_CONFIG_NUM_KEYS` whitelist):
`mobile_enabled`, `d_min_cm`, `d_max_cm`, `cbf_gamma`, `control_hz`,
`odom_hz`, `motion_cmd_ttl_ms`, `max_v_mps`, `max_w_rps`.
`dashboard/canis_config.py` mirrors the same values for the sim fallback.

---

## 4. Stationary vs. mobile mode semantics

`CANIS_MOBILE_ENABLED` is a compile-time flag in `canis_config.h`, announced
live via `/config` (`mobile_enabled`) and `/ingest` (`mobile`).

**Stationary (`CANIS_MOBILE_ENABLED = 0`, SAFE DEFAULT — ships this way):**
- The ESP32 issues **NO drive commands from the new stack**: it never polls
  `/motion`, never emits `VEL`, never streams `/telemetry`, never touches the
  encoder pins.
- Everything that exists today keeps running EXACTLY as today: YOLO detection
  → `/detect` → `/command` → arousal/state machine, the dispenser + ACK/JAM
  path, the dashboard, and all existing fail-safes (exposure gate,
  arm→confirm, `CANIS_NEVER_APPROACH`, Uno watchdog, session caps).
- Legacy motion (roam / orient / flee) remains governed solely by its existing
  flags (`CANIS_ROAM_ENABLED`, exposure level, mode g/p/b) — this contract
  neither enables nor disables it.
- New `/ingest` fields are omitted (§1.1), so the wire traffic is
  byte-identical to the pre-upgrade system.

**Mobile (`CANIS_MOBILE_ENABLED = 1`):**
- Drive commands come ONLY from the CBF pipeline of
  `docs/SAFE_MOTION_CBF_SPEC.md`: Pi estimator + comfort margin + QP → §2.1
  `MOVE v omega` → §2.2 clamps → §2.3 `VEL` — replacing the legacy open-loop
  roam word-commands as the source of locomotion.
- Every pre-existing fail-safe stays in force and OUTRANKS the CBF: exposure
  gate (a level that forbids motion still forbids it), `CANIS_NEVER_APPROACH`
  (compile-guarded to be 1 in mobile builds), the Uno 1000 ms watchdog, and
  power-cycle → exposure 0.
- Degradation is one-way toward stationary: ToF/vision dropout ⇒ treat
  `d_rd` as at-the-boundary ⇒ no distance-reducing motion; `/motion` or
  `/telemetry` unreachable ⇒ STOP and behave as stationary. **No single
  sensor or compute failure may produce a distance-reducing command.**

---

*Any field, verb, endpoint, unit, or sign convention not listed here is out of
contract. Add it here first.*

---

## 5. Bark contract: "treat when he stops barking" (Pi audio detector -> dashboard -> ESP32)

Added 2026-10. A separate Pi process, `audio/detect_bark.py`, listens to a Bluetooth microphone, decides with
`audio/bark_logic.py` when Shifu has barked and when he has then been quiet for `CANIS_BARK_QUIET_S` seconds,
and reports to the dashboard. The robot never moves because of this: it only triggers the existing `reward()`.

### 5.1 Pi detector -> dashboard: `POST /bark`  (token-gated like `/detect`)
Body: `{"event": "bark" | "quiet" | "mic_lost" | "mic_ok", ...}`.
- `bark`: optional `duration_s`, `peak_db` (logged for the study as event `bark`).
- `quiet`: optional `barks` (count in the episode). Logged as `bark_quiet`. **Refused (`accepted:false`) while the
  mic is marked lost**: silence from a dead microphone is not a quiet dog.
- `mic_lost` / `mic_ok`: the detector reports a missing stream (no audio for 3 s, or digital silence for 6 s).
- Anything else -> 400. `GET /api/bark` (public, read-only) returns the current state.

### 5.2 Dashboard -> ESP32: two extra response HEADERS on `GET /command` (body unchanged)
- `X-CANIS-Quiet-Seq: <int>`: epoch-seconds counter, strictly increasing, `0` until the first accepted quiet.
- `X-CANIS-Quiet-Age: <float>`: seconds since that quiet event, `-1` if none.
Old firmware ignores unknown headers, so this is backward compatible.

### 5.3 ESP32 rule (LAW)
The first `Quiet-Seq` seen after boot is only a **baseline**. A later, larger value with `0 <= Age <= CANIS_BARK_MAX_AGE_S`
calls `reward("quiet after barking")`, which keeps its normal cooldown, daily cap, jam handling and observe-only
suppression. A larger value with an older age is ignored. Disable with `CANIS_BARK_REWARD_ENABLED 0` (do this
during the study's baseline week so that no contingency is active).
