# CANIS — Architecture

Diagrams for the full system as implemented in `optimized/`: the original
behavior plus the `[OPT-n]` hardening (`optimized/OPTIMIZATIONS.md`) and the
mobile-drivetrain / YOLO / CBF upgrade (§§5–7 below; `docs/MOBILE_UPGRADE_STATUS.md`).
§§1–4 describe the system that ships **today** (stationary, `CANIS_MOBILE_ENABLED
= 0`); §§5–7 describe the mobile/camera upgrade layered on top, inert until the
flag is set. All Mermaid — renders on GitHub.

---

## 1. System block diagram (compute, links, power)

```mermaid
flowchart TB
    subgraph PI["Raspberry Pi 5 — eyes + face"]
        CAM["camera (per CANIS_CAMERA_MODE):<br/>A onboard CSI/USB · B ESP32-CAM MJPEG<br/>C fixed room camera"]
        DET["vision/detect_yolo.py (DEFAULT)<br/>YOLO11n-NCNN dog detector (COCO 'dog')<br/>bearing + monocular range → /detect<br/>blur guard · grace [OPT-12] · frozen guard [OPT-13]<br/>(detect.py ArUco kept as fallback;<br/>detect_fixed.py serves mode C)"]
        DASH["dashboard/app.py (waitress)<br/>/ingest /command /detect /api/state<br/>+ mobile: /telemetry /motion<br/>event log [OPT-14] · CSV export"]
        LOG[("events.jsonl<br/>append-only")]
        CAM --> DET
        DET -- "POST /detect {zone}" --> DASH
        DASH --> LOG
    end

    subgraph ESP["ESP32 DevKit V1 — brain"]
        SENS["PIR (removed)<br/>3× ultrasonic L/C/R<br/>TRIG 32/27/14 · ECHO 34/25/35"]
        CONF["confirmation filter<br/>(3 consecutive reads)"]
        FSM["arousal integrator + state machine<br/>CALM / AROUSAL / CORRECTION<br/>hysteresis [OPT-3] · dt clamp [OPT-4]<br/>vision TTL [OPT-9]"]
        GATE["EXPOSURE_LEVEL gate 0/1/2<br/>(welfare knob; level-1 never-approach [OPT-8])"]
        BZ1["buzzer GPIO33<br/>800 Hz disengage cue"]
        SENS --> CONF --> FSM
        FSM --> GATE
        FSM --> BZ1
    end

    subgraph UNO["Arduino Uno — limbs (dumb, real-time)"]
        WD["motion watchdog<br/>auto-STOP after 1000 ms<br/>without a fresh command"]
        L298["L298N driver"]
        M1["left motor"]
        M2["right motor"]
        SRV["SG90 gate servo (pin 9)"]
        BZ2["marker buzzer 2 kHz (pin 6)"]
        BEAM["IR beam-break @ chute exit<br/>(A0, jam detection [OPT-11])"]
        WD --> L298
        L298 --> M1
        L298 --> M2
    end

    DASH <-- "POST /ingest (1 Hz + on change)<br/>GET /command (1 Hz poll)<br/>WiFi · token [OPT-2]" --> FSM
    GATE -- "UART 9600 one-way + ACK/JAM return<br/>GPIO13 → pin10 · pin11 → GPIO4 (level-shifted)<br/>FORWARD/LEFT/RIGHT/STOP/DISPENSE<br/>re-pulsed every 500 ms" --> WD
    FSM -- "DISPENSE" --> SRV
    BZ2 -. "marker precedes drop" .-> SRV
    SRV -- "treat falls" --> BEAM

    subgraph PWR["Power (untethered plan — see hardware/WIRING.md)"]
        BANK["USB bank ≥ 3 A → Pi 5<br/>(WiFi-only: no shared ground needed)"]
        PACK["motor pack 6×AA ≈ 9 V / 2S Li-ion → L298N +12V"]
        BUCK["LM2596 buck → 5 V<br/>ESP32 VIN · Uno 5V · sensors<br/>⚠ set to 5.0–5.1 V BEFORE connecting"]
        PACK --> BUCK
    end
    BANK --> PI
    PACK --> L298
    BUCK --> ESP
    BUCK --> UNO
```

Robot-side grounds (ESP32, Uno, sensors, L298N, buck, pack −) are **common**;
the Pi is electrically isolated on its own bank (WiFi-only link).

Failure-domain property this split buys: the *executor* (Uno) needs nothing
alive to stop safely — a crashed brain, dropped wire, or dead WiFi all converge
on "motors stop within ~1 s" (see `docs/SAFETY.md`).

---

## 2. Arousal state machine

Arousal (0–100) is rate-driven: **+30/s** while any confirmed detection is
active (PIR ∨ ultrasonic-zone ∨ vision), **−12/s** always ⇒ net +18/s during
presence, −12/s after departure. Enter/exit thresholds form a Schmitt trigger.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> CALM

    CALM --> AROUSAL : arousal ≥ 20 (ENTER)
    AROUSAL --> CORRECTION : arousal ≥ 70 (ENTER)
    CORRECTION --> AROUSAL : arousal < 60 (EXIT)
    AROUSAL --> CALM : arousal < 12 (EXIT) — 🍖 REWARD (dog disengaged)
    CORRECTION --> CALM : arousal < 12 — 🍖 REWARD + immediate STOP

    CALM : CALM — baseline
    CALM : rewards sustained calm (30 s interval,
    CALM : post-warm-up; 20 s cooldown, 30/session cap)

    AROUSAL : AROUSAL — watch only
    AROUSAL : no intervention of any kind

    CORRECTION : CORRECTION — intervene every 2 s
    CORRECTION : 800 Hz cue (always)
    CORRECTION : exposure 0 → cue only
    CORRECTION : exposure 1 → + pivot toward zone (700 ms, never FORWARD [OPT-8])
    CORRECTION : exposure 2 → + brief approach (400 ms)
```

Timing consequences (unit-tested in `tests/test_state.py`):
sustained presence reaches CORRECTION in ≈ 3.9 s; after departure, release to
CALM (⇒ reward) takes ≈ 4.8 s of decay plus sensor-clear time. A 1 s spurious
blip peaks below 20 and never intervenes.

---

## 3. Treat-dispense sequence (with delivery verification)

```mermaid
sequenceDiagram
    participant FSM as ESP32 state machine
    participant UART as UART link (9600)
    participant UNO as Arduino Uno
    participant DOG as Shifu

    FSM->>FSM: AROUSAL/CORRECTION → CALM transition
    FSM->>FSM: gates: dispenser not FAULTED [OPT-11],<br/>cooldown 20 s ok, session cap < 30 ok
    FSM->>UART: "DISPENSE\n"
    FSM->>FSM: awaitingAck = true (deadline 2.5 s) [OPT-7]
    UART->>UNO: DISPENSE
    UNO->>UNO: stopMotors() (frees Timer2; settle before reward)
    UNO->>DOG: 2 kHz marker, 120 ms
    UNO->>UNO: wait 150 ms (marker→drop gap)
    UNO->>UNO: gate servo 80°→0°, hold 150 ms, close

    alt beam-break sees the treat fall [OPT-11]
        UNO->>UNO: beam interrupted within 600 ms ✓
        UNO->>UART: "ACK\n"
        UART->>FSM: ACK
        FSM->>FSM: rewardCount++ (counts DELIVERY, not intent)
        DOG->>DOG: eats treat (leaving was reinforced)
    else no drop seen
        UNO->>UNO: retry: re-open gate +100 ms, wait 600 ms
        alt retry drops
            UNO->>UART: "ACK\n"
        else still nothing
            UNO->>UART: "JAM\n"
            UART->>FSM: JAM
            FSM->>FSM: jamCount++; 2 consecutive ⇒ FAULTED:<br/>suppress all further auto-rewards<br/>(no marker without a treat) until operator 'd'
        end
    end
```

With `JAM_SENSOR_ENABLED 0` (beam-break not yet installed) the Uno ACKs after
the gate cycle — the pre-[OPT-11] behavior. With `CANIS_USE_DISPENSE_ACK 0`
the ESP32 reverts to count-on-send (no return wire needed).

---

## 4. Dashboard data flow (live view + study record)

```mermaid
flowchart LR
    ESP["ESP32<br/>state, arousal, counters,<br/>jam_count, exposure_level"]
    VIS["vision detect.py"]
    subgraph PI5["Pi — dashboard/app.py"]
        ING["/ingest POST<br/>token + validation [OPT-2]"]
        CMD["/command GET (plain text)"]
        DETR["/detect POST"]
        LIVE["LiveState (locked)<br/>latest snapshot + Pi wall-clock<br/>stamping of last_reward_time"]
        SIM["Simulator fallback<br/>(same logic, same schema)"]
        API["/api/state GET"]
        EVLOG["event_log.py [OPT-14]<br/>derives events from deltas:<br/>state_transition · treat_dispensed ·<br/>dispenser_jam · zone_seen ·<br/>exposure_change · firmware_reboot · link_gap"]
        J[("events.jsonl")]
        EXP["/export/events.csv<br/>/export/sessions.csv<br/>/api/events"]
    end
    UI["browser (phone)<br/>1 Hz poll · state card ·<br/>jam fault banner · exposure level"]
    STUDY["study/analyze_sessions.py<br/>(same aggregation code)"]

    ESP -- "1 Hz + on change" --> ING --> LIVE
    ING --> EVLOG --> J
    VIS -- "zone @ ≤1 Hz while seen" --> DETR --> EVLOG
    DETR --> CMDSTORE["Command (TTL 3 s)"]
    CMDSTORE --> CMD -- "1 Hz poll" --> ESP
    LIVE --> API --> UI
    SIM -. "only when ESP32 silent > 5 s<br/>(footer says so)" .-> API
    J --> EXP --> STUDY
```

Clock ownership: the ESP32 has no wall clock and sends only counters/uptime;
every timestamp in the record is stamped by the Pi at arrival. The event log is
append-only JSONL (schema: `dashboard/event_log.py` docstring), and the session
aggregation used by `/export/sessions.csv` and by the offline analyzer is the
**same function**, so the two can never disagree.

---

## 5. Compute tiers (3-tier split) + the mobile / CBF upgrade

Responsibility is split across three processors by *how fast* and *how
certifiable* each duty must be. The split is unchanged by the mobile upgrade —
the new work lands where it fits the existing tier contract, and none of it runs
unless `CANIS_MOBILE_ENABLED = 1` (SAFE DEFAULT `0`).

| Tier | Device | Existing duty | Added by the mobile/CBF upgrade |
|---|---|---|---|
| **Real-time** | Arduino Uno | dumb executor: motor PWM via L298N, gate servo, marker tone, beam-break; **1 s motion watchdog** | new `VEL <pwmL> <pwmR>` verb (differential drive); does NO kinematics — stays a certifiable executor. Watchdog **unchanged and supreme** |
| **Coordinating** | ESP32 | PIR + 3× ultrasonic, confirmation filter, arousal FSM, exposure gate, buzzer, dispenser logic, WiFi link | reads 2× quadrature encoders on the **PCNT** peripheral (zero-CPU hardware counting); computes odometry `(v, ω)`; streams `/telemetry` @10 Hz; polls `/motion`, clamps `\|v\|/\|ω\|`, re-clamps `v ≤ 0` at `D_MIN`, maps to per-wheel PWM |
| **Host** | Raspberry Pi 5 | vision (`detect_yolo.py`), dashboard, event log, study export | `optimized/control/`: EKF/complementary state estimator (odom ⊕ IMU ⊕ ToF ⊕ YOLO bearing), adaptive `D_comfort(s)`, the closed-form **CBF-QP** safety filter, reward-on-approach |

Why encoders land on the ESP32, not the Uno: the Uno's two hardware interrupts
get tight with two quadrature channels each; the ESP32 PCNT counts in hardware.
This keeps the Arduino a pure executor — the failure-domain property from §1
(the executor needs nothing else alive to stop safely) is preserved.

```mermaid
flowchart LR
    subgraph PI5["Pi 5 — host (mobile additions)"]
        EST["control/estimator.py<br/>EKF/complementary:<br/>odom ⊕ IMU ⊕ ToF ⊕ YOLO bearing<br/>→ d_rd, β, pose"]
        CMF["control/comfort.py<br/>adaptive D_comfort(s) ∈ [D_MIN,D_MAX]"]
        CBF["control/cbf.py<br/>closed-form CBF-QP<br/>u* = proj(u_nom): v·cosβ ≤ γ·h"]
        RWD["control/reward.py<br/>voluntary-approach detector"]
    end
    TEL["ESP32 /telemetry @10 Hz<br/>enc_l/enc_r/odom_v/odom_omega/tof_m/heading"]
    DETY["detect_yolo.py /detect<br/>bearing_deg · distance_m"]
    MOT["/motion → 'MOVE v ω' | 'STOP'"]
    TEL --> EST
    DETY --> EST
    EST --> CMF --> CBF
    EST --> CBF
    CBF --> MOT
    EST --> RWD
    RWD -. "on_reward callback" .-> MOT
    MOT --> ESP32X["ESP32: clamp + v≤0 at D_MIN<br/>→ VEL pwmL pwmR"] --> UNOX["Uno: L298N (1 s watchdog)"]
```

The barrier is the new **Layer 3** in `docs/SAFETY.md`; wire formats are LAW in
`docs/INTERFACE_CONTRACT.md`; the math is `docs/SAFE_MOTION_CBF_SPEC.md`.

---

## 6. Two operating modes — stationary (default) vs. mobile

One codebase, one compile-time switch (`CANIS_MOBILE_ENABLED`, announced live via
`/config` and `/ingest`). The mode changes only whether the drivetrain stack is
active; every existing fail-safe is in force in both.

```mermaid
flowchart TB
    SW{"CANIS_MOBILE_ENABLED"}
    SW -- "0 — SAFE DEFAULT<br/>(ships this way; this week's study)" --> STAT
    SW -- "1 — CBF differential drive<br/>(encoder motors wired in)" --> MOB
    subgraph STAT["STATIONARY"]
        S1["no /motion poll · no VEL · no /telemetry<br/>encoder pins untouched"]
        S2["YOLO → /detect → /command → arousal FSM<br/>dispenser · dashboard · all fail-safes UNCHANGED"]
        S3["/ingest is byte-identical to the<br/>pre-upgrade wire shape (new fields omitted)"]
        S4["legacy motion (roam/orient/flee) still<br/>governed only by its own flags"]
    end
    subgraph MOB["MOBILE"]
        M1["locomotion source = CBF pipeline ONLY<br/>estimator → D_comfort → QP → MOVE → VEL"]
        M2["replaces legacy open-loop roam words"]
        M3["every prior fail-safe OUTRANKS the CBF:<br/>exposure gate · NEVER_APPROACH (compile-guarded)<br/>· Uno 1 s watchdog · power-cycle→exposure 0"]
        M4["degradation is one-way toward stationary:<br/>sensor/link loss ⇒ no distance-reducing cmd / STOP"]
    end
```

**Stationary is the shipped default** and is what runs the conditioning study
this week — no encoder motors required. **Mobile** activates only once the
encoder gearmotors are wired in and the flag is flipped; the compile guard
`CANIS_MOBILE_ENABLED && !CANIS_NEVER_APPROACH → #error` forbids a mobile build
that could drive at the dog. Full semantics: `INTERFACE_CONTRACT.md` §4.

---

## 7. Camera architectures A / B / C (`CANIS_CAMERA_MODE`)

Perception is now a **YOLO dog detector** (`detect_yolo.py`, COCO `dog` class) —
no marker on the dog. ArUco is retained in two places: `detect.py` as a
backward-compatible fallback reporter, and **on the robot** in mode C, where a
DICT_4X4_50 marker (`CANIS_ROBOT_MARKER_ID`=17) localizes the rigid robot while
YOLO finds the dog. All three modes POST the *same* detection contract (§3 of
`INTERFACE_CONTRACT.md`), so the ESP32, controller, and dashboard are
camera-mode-agnostic — you pick by setting one constant, not by rewriting.

```mermaid
flowchart TB
    subgraph A["A — onboard (SAFE/simple DEFAULT)"]
        A1["camera on the Pi (picamera2 / USB)"]
        A2["detect_yolo.py — robot's-eye"]
        A1 --> A2
    end
    subgraph B["B — esp32cam_stream"]
        B1["ESP32-CAM OV2640<br/>MJPEG @ :81/stream"]
        B2["off-board Pi pulls frames over WiFi<br/>detect_yolo.py (same guards, frame SOURCE swapped)"]
        B1 -. "WiFi (flaky — safety stays on onboard ToF)" .-> B2
    end
    subgraph C["C — fixed_room"]
        C1["fixed room camera → off-board Pi"]
        C2["detect_fixed.py — room-frame geometry:<br/>YOLO=dog on floor plane,<br/>ArUco marker=robot pose+heading"]
        C1 --> C2
    end
    A2 & B2 & C2 --> DETPOST["POST /detect<br/>zone · dog_present · bearing_deg · distance_m · confidence"]
```

| | A `onboard` | B `esp32cam_stream` | C `fixed_room` |
|---|---|---|---|
| View | robot's-eye | robot's-eye | fixed, room-frame |
| Pi location | on the robot | off-board | off-board |
| New hardware | none | 1× ESP32-CAM | fixed camera mount |
| Robot identity | — | — | ArUco marker on robot |
| Reliability | high | streaming is flaky | highest (best study data) |
| Bearing sign | camera frame | camera frame | **robot frame** (preserves `zoneToDir()` left/right) |
| Failure fail-safe | grace → silence | reconnect; **STOP** on stale cmd | marker lost → `distance_m` null; **STOP** on stale cmd |
| When (recommended) | this week (start here) | long-term / room-portable | when going mobile in one room |

Modes B and C put the Pi off-board, so the drive command rides WiFi — see
`SAFETY.md` Layer 3.4 for the added WiFi/stream failure mode (fail-safe: STOP).
Detailed recommendation and sequencing: `docs/MOBILE_UPGRADE_STATUS.md` §5b.
