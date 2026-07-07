# CANIS — Architecture

Diagrams for the full system as implemented in `optimized/` (behavior identical
to the original tree plus the `[OPT-n]` hardening documented in
`optimized/OPTIMIZATIONS.md`). All Mermaid — renders on GitHub.

---

## 1. System block diagram (compute, links, power)

```mermaid
flowchart TB
    subgraph PI["Raspberry Pi 5 — eyes + face"]
        CAM["Pi Camera OV5647<br/>(CSI ribbon)"]
        DET["vision/detect.py<br/>ArUco DICT_4X4_50, marker ID 0<br/>blur guard · grace [OPT-12] · frozen-frame guard [OPT-13]"]
        DASH["dashboard/app.py (waitress)<br/>/ingest /command /detect /api/state<br/>event log [OPT-14] · CSV export"]
        LOG[("events.jsonl<br/>append-only")]
        CAM --> DET
        DET -- "POST /detect {zone}" --> DASH
        DASH --> LOG
    end

    subgraph ESP["ESP32 DevKit V1 — brain"]
        SENS["PIR (GPIO27)<br/>3× ultrasonic L/C/R<br/>TRIG 32/26/14 · ECHO 34/25/35"]
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
