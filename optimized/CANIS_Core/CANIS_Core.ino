/*
  CANIS — Core (ESP32)  [OPTIMIZED DUPLICATE]
  ===========================================
  Functionally identical loop to the original CANIS_Core/CANIS_Core.ino, with
  six review-identified robustness fixes layered in. The behavioral pipeline is
  unchanged:

      world -> sensors -> confirmation -> arousal -> state machine -> dashboard

  WHAT'S DIFFERENT vs the original (each tagged [OPT-n] inline):
    [OPT-1] All tunables come from canis_config.h (single source of truth). No
            more copy-pasted / drifted constants. (orig CANIS_Core.ino:69-112)
    [OPT-3] State machine uses ENTER/EXIT hysteresis (Schmitt trigger) so a dog
            hovering at 70 or 20 stops chattering interventions.
            (orig updateState CANIS_Core.ino:306-332)
    [OPT-4] updateArousal clamps dt so a WiFi/HTTP stall can't teleport arousal
            into CORRECTION in one tick. (orig updateArousal CANIS_Core.ino:296)
    [OPT-5] Non-blocking WiFi reconnect watchdog in loop() — a post-boot drop no
            longer disables reportToPi/pollCommand forever. (orig had connect-once
            only, connectWiFi CANIS_Core.ino:200)
    [OPT-7] Optional dispense-ACK: reward_count increments only when the Arduino
            confirms a completed drop, so the count reflects DELIVERY not intent.
            (orig reward() counted on send, CANIS_Core.ino:445-446)
    [C5]    Dispense correlation id (TECH_DEBT #4 / ROADMAP C5). Each reward
            DISPENSE carries an incrementing id ("DISPENSE <n>"); the Arduino
            echoes it ("ACK <n>" / "JAM <n>") and serviceAck() only counts a
            reply whose id matches the outstanding request. A reply with a stale
            or sentinel id — e.g. "ACK 0" from a local button-press dispense that
            lands inside our wait window — is logged and DISCARDED, not counted,
            fixing the documented ±1 miscount. Manual 'd' test-fires as id 0.
    [OPT-9] Vision-latch TTL: visionActive now expires locally if no successful
            /command poll refreshes it within CANIS_VISION_TTL_MS. The original
            could hold arousal up FOREVER if WiFi/the Pi died while the latch
            was set — a permanent phantom CORRECTION (buzzer + motion at
            exposure >= 1) at a dog that had already left.
            (orig pollCommand CANIS_Core.ino:497-531 was the only clear path)
    [OPT-10] HTTP deferred while a motion is active. reportToPi/pollCommand can
            each block up to CANIS_HTTP_TIMEOUT_MS; back-to-back with WiFi
            latency that can stretch the watchdog-feed gap past the Arduino's
            1000 ms MOTION_TIMEOUT_MS and stutter an intended orient. Motions
            are short (<= 700 ms), so pausing HTTP during them costs nothing.
    [OPT-11] Dispenser jam handling: the Arduino can now reply "JAM" (beam-break
            drop sensor saw no treat fall, even after a retry). After
            CANIS_JAM_FAULT_THRESHOLD consecutive JAMs the ESP32 marks the
            dispenser FAULTED and suppresses further treat requests — a marker
            tone with no treat actively damages the conditioning. Manual 'd'
            clears the fault. jam_count is reported to the dashboard.
    [B5]    Observation-only build (CANIS_OBSERVE_ONLY, ROADMAP B5). When the
            compile flag is 1 the full sense->arousal->state->report pipeline
            runs untouched but EVERY actuator is gated off (no treat, no buzzer,
            no motion). Replaces the "unplug the link / empty the hopper" hack
            the baseline week used for its overnight false-positive probe: the
            state trace is still logged, so you measure what the robot WOULD have
            done with zero risk of a stray cue reaching the dog.
    [C1]    Runtime-config anti-drift (ROADMAP C1). At boot (and after a WiFi
            reconnect) the ESP32 POSTs the tunables it is ACTUALLY running to the
            dashboard's /config route, so the dashboard shows firmware truth
            instead of a hand-copied canis_config.py guess (which becomes the
            sim/offline fallback only). Kills the last drift class (§6.3).
    [C4]    Defensive exposure change (ROADMAP C4 / §1.7). The ORIGINAL cycled
            0->1->2->0 on a single 'm' press, so a stray double-press from 0
            landed on 2 (approach) unconfirmed. Now 'm' only ARMS the next level
            and a distinct 'y' commits it, within CANIS_EXPOSURE_CONFIRM_MS; an
            un-confirmed arm auto-expires so it can't be committed later.

  NOTE: no arduino_secrets.h value is reproduced here; the include and the
  SECRET_* names are the same public interface as the original.

  Serial Monitor @ 115200.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <ctype.h>             // tolower() for zone-label matching
#include <stdlib.h>            // atoi() for parsing the ACK/JAM correlation id [C5]
#include "canis_config.h"      // [OPT-1] single source of truth (symlink -> ../canis_config.h)
#include "arduino_secrets.h"   // WiFi/Pi creds — gitignored, see arduino_secrets.h.example

// ---------- WiFi / Pi ----------
const char* WIFI_SSID = SECRET_SSID;
const char* WIFI_PASS = SECRET_PASS;
const char* PI_HOST   = SECRET_PI_HOST;
const int   PI_PORT   = CANIS_PI_PORT;

// ---------- Shared-secret token for the dashboard (mirror of app.py token) ----
// Sent on every mutating request so the Pi can reject spoofed /ingest traffic.
// Define SECRET_CANIS_TOKEN in arduino_secrets.h; falls back to empty (dev).
#ifndef SECRET_CANIS_TOKEN
#define SECRET_CANIS_TOKEN ""
#endif
const char* CANIS_TOKEN = SECRET_CANIS_TOKEN;

// ---------- Sensor pins ----------
const int PIR_PIN  = 27;   // PIR OUT (3.3V logic, read directly)
const int   US_TRIG[3] = { 32, 26, 14 };          // LEFT, CENTRE, RIGHT
const int   US_ECHO[3] = { 34, 25, 35 };          // LEFT, CENTRE, RIGHT
const char* US_DIR[3]  = { "LEFT", "FORWARD", "RIGHT" };
const int   NUM_US = CANIS_NUM_US;

// ---------- Arduino dispenser link ----------
// [OPT-7] The original wired TX only (ARD_RX_PIN = -1) to free GPIO14 for the
// third ultrasonic. To receive the dispense ACK we need a real RX. GPIO4 is
// unused in this design, so ACK does NOT cost us the third sensor.
//   ESP32 GPIO 13 (TX2) -> Arduino pin 10 (SoftwareSerial RX)   [3.3V->5V OK]
//   Arduino pin 11 (SoftwareSerial TX) -> ESP32 GPIO 4 (RX2)    [5V->3.3V!!]
// WARNING: the Arduino->ESP32 direction is 5V into a 3.3V pin. It MUST go
// through a level shifter or a 2:1 resistor divider (e.g. 1k/2k). See
// OPTIMIZATIONS.md. If you don't wire the return line, set
// CANIS_USE_DISPENSE_ACK to 0 in canis_config.h and behavior reverts to
// count-on-send.
const int ARD_TX_PIN = 13;
#if CANIS_USE_DISPENSE_ACK
const int ARD_RX_PIN = 4;    // ESP32 RX2 <- Arduino TX (through level shift)
#else
const int ARD_RX_PIN = -1;   // unused (frees GPIO14 for US_TRIG[2])
#endif

// ---------- Buzzer ----------
const int BUZZER_PIN = 33;

// ---------- Confirmation filter (from Sensors_Test) ----------
struct Confirmed {
  bool state = false;
  int  posStreak = 0;
  int  negStreak = 0;
  bool update(bool raw) {
    if (raw) { posStreak++; negStreak = 0; }
    else     { negStreak++; posStreak = 0; }
    bool before = state;
    if (!state && posStreak >= CANIS_CONFIRM_N) state = true;
    if ( state && negStreak >= CANIS_CONFIRM_N) state = false;
    return state != before;
  }
};

Confirmed pir;
Confirmed usZone[3];
float     usCm[3] = { -1, -1, -1 };
bool      zoneActive = false;
bool      detectionActive = false;

// ---------- State ----------
enum State { CALM, AROUSAL, CORRECTION };
State state = CALM, prevState = CALM;

float arousal = 0.0;
unsigned long lastTick = 0, calmSince = 0, lastCalmReward = 0;
unsigned long lastCorrectionAction = 0, lastLog = 0, lastReport = 0;
unsigned long bootTime = 0, lastSample = 0;

int rewardCount = 0;
int interventionCount = 0;
unsigned long lastRewardMs = 0;

unsigned long lastPoll = 0;
bool visionActive = false;
char visionZone[24] = "";
unsigned long lastVisionRefresh = 0;   // [OPT-9] last successful /command poll that saw the dog

// ---------- Movement sub-behavior ----------
char          moveCmd[12]    = "";
unsigned long moveUntil      = 0;
unsigned long lastMovePulse  = 0;
int           motionPhase    = 0;

int EXPOSURE_LEVEL = CANIS_EXPOSURE_DEFAULT;

// ---------- Exposure two-step ARM->CONFIRM [C4] ----------
// 'm' proposes the next level here (does NOT change the live EXPOSURE_LEVEL);
// 'y' commits it iff still within CANIS_EXPOSURE_CONFIRM_MS. -1 = nothing armed.
int           proposedExposure = -1;
unsigned long exposureArmedAt  = 0;

// ---------- WiFi reconnect watchdog [OPT-5] ----------
unsigned long lastWifiCheck = 0;

// ---------- Boot config announce [C1] ----------
// Latches true after the ESP32 has POSTed its live tunables to /config; the
// wifi watchdog clears it on a reconnect so config is re-announced after an
// outage. See postConfig().
bool configPosted = false;

// ---------- Dispense ACK bookkeeping [OPT-7] ----------
#if CANIS_USE_DISPENSE_ACK
bool          awaitingAck   = false;
unsigned long ackDeadline   = 0;
char          ackBuf[12]    = "";   // [C5] room for "ACK <id>" / "JAM <id>"
int           ackLen        = 0;
int           reqIdSeq        = 0;  // [C5] last-issued reward correlation id (0 reserved as sentinel)
int           outstandingReqId = 0; // [C5] id we're awaiting an ACK/JAM for (0 = none outstanding)
#endif

// ---------- Dispenser fault policy [OPT-11] ----------
int  jamCount         = 0;      // lifetime JAMs this session (reported to Pi)
int  consecJams       = 0;      // consecutive JAMs; resets on ACK
bool dispenserFaulted = false;  // true -> suppress treat requests until 'd'

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PIR_PIN, INPUT_PULLDOWN);
  for (int i = 0; i < NUM_US; i++) {
    pinMode(US_TRIG[i], OUTPUT);
    pinMode(US_ECHO[i], INPUT);
    digitalWrite(US_TRIG[i], LOW);
  }
  pinMode(BUZZER_PIN, OUTPUT);

  Serial2.begin(9600, SERIAL_8N1, ARD_RX_PIN, ARD_TX_PIN);

  Serial.println("\nCANIS core online (optimized). PIR warming up (~60s).");
  Serial.println("Keys:  t=trigger  r=reset  d=dispense  m=arm exposure  y=confirm exposure  f=test fwd  s=stop  c=self-test");
#if CANIS_OBSERVE_ONLY
  Serial.println("*** OBSERVE-ONLY BUILD [B5]: sensing + reporting only — NO treat/buzzer/motion ***");
#endif
  Serial.print  ("Exposure level: "); Serial.println(EXPOSURE_LEVEL);
  connectWiFi();

  bootTime = millis();
  lastTick = millis();
  calmSince = millis();
  lastWifiCheck = millis();
}

void loop() {
  unsigned long now = millis();
  float dt = (now - lastTick) / 1000.0;
  lastTick = now;

  handleInput();
  readSensors(now);
  wifiWatchdog(now);      // [OPT-5] keep the link alive without blocking
  postConfig(now);        // [C1] announce live tunables to the Pi (once per link)
  pollCommand(now);
  serviceVisionTtl(now);  // [OPT-9] expire a stale vision latch locally
  updateArousal(dt);
  updateState();
  runStateBehavior(now);
  serviceMotion(now);
#if CANIS_USE_DISPENSE_ACK
  serviceAck(now);        // [OPT-7] count rewards only on confirmed delivery
#endif
  logStatus(now);
  reportToPi(now);

  delay(20);
}

// ---------- WiFi ----------
void connectWiFi() {
  Serial.print("WiFi: connecting to "); Serial.println(WIFI_SSID);
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(300); Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("\nWiFi connected. ESP32 IP: "); Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi FAILED — running offline (Serial only). Check SSID/PASS.");
  }
}

// [OPT-5] Non-blocking reconnect watchdog. The original connected once in
// setup(); if the AP dropped after boot, reportToPi()/pollCommand() were dead
// forever. This periodically checks the link and kicks a non-blocking
// WiFi.reconnect() — no busy-wait, so the control loop never stalls.
void wifiWatchdog(unsigned long now) {
  if (now - lastWifiCheck < CANIS_WIFI_CHECK_MS) return;
  lastWifiCheck = now;
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("  [NET] WiFi down -> reconnect()");
    WiFi.reconnect();     // returns immediately; status flips when it succeeds
    configPosted = false; // [C1] re-announce live config once the link is back
  }
}

// ---------- Manual override ----------
void handleInput() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 't') {
      arousal += CANIS_TRIGGER_BUMP;
      if (arousal > CANIS_AROUSAL_MAX) arousal = CANIS_AROUSAL_MAX;
      Serial.print("  [MANUAL] trigger -> arousal "); Serial.println(arousal, 1);
    } else if (c == 'r') {
      arousal = 0;
      Serial.println("  [MANUAL] reset -> arousal 0");
    } else if (c == 'd') {
      // [OPT-11] Manual dispense doubles as the operator's fault-clear: after
      // fixing the hopper, press 'd' to test-fire and re-enable auto rewards.
      if (dispenserFaulted) {
        dispenserFaulted = false;
        consecJams = 0;
        Serial.println("  [MANUAL] dispenser fault CLEARED by operator");
      }
      Serial2.println("DISPENSE 0");   // [C5] id 0 = operator test-fire, not a tracked reward
      Serial.println("  [MANUAL] sent DISPENSE 0 (test-fire) to Arduino");
    } else if (c == 'm') {
      // [C4] ARM the next exposure level — does NOT change the live level yet.
      // A distinct 'y' within CANIS_EXPOSURE_CONFIRM_MS is required to commit it,
      // so a stray double-press can no longer land on level 2 (approach).
      // Repeated 'm' cycles the PROPOSAL (0->1->2->0) so any target level can be
      // armed directly from any live level; a stale proposal restarts from live.
      if (proposedExposure >= 0 &&
          (long)(millis() - exposureArmedAt) <= (long)CANIS_EXPOSURE_CONFIRM_MS) {
        proposedExposure = (proposedExposure + 1) % 3;
      } else {
        proposedExposure = (EXPOSURE_LEVEL + 1) % 3;
      }
      exposureArmedAt  = millis();
      Serial.print("  [MANUAL] exposure ARMED -> "); Serial.print(proposedExposure);
      Serial.print(" (press 'y' within "); Serial.print(CANIS_EXPOSURE_CONFIRM_MS / 1000);
      Serial.println("s to confirm)");
    } else if (c == 'y') {
      // [C4] CONFIRM the armed change, but only if one is armed AND still fresh;
      // a timed-out proposal is discarded so it can never commit by surprise.
      if (proposedExposure >= 0 &&
          (long)(millis() - exposureArmedAt) <= (long)CANIS_EXPOSURE_CONFIRM_MS) {
        EXPOSURE_LEVEL = proposedExposure;
        configPosted   = false;   // [C1] a level change is config truth -> re-announce
        Serial.print("  [MANUAL] exposure level -> "); Serial.println(EXPOSURE_LEVEL);
      } else {
        Serial.println("  [MANUAL] 'y' ignored — no armed exposure change (arm with 'm' first)");
      }
      proposedExposure = -1;
    } else if (c == 'f') {
      startMotion("FORWARD", 1500, millis());
      motionPhase = 0;
      Serial.println("  [MANUAL] test FORWARD (1.5s)");
    } else if (c == 's') {
      stopMotion(millis());
      Serial.println("  [MANUAL] STOP");
    } else if (c == 'c') {
      // [C7] Guided commissioning self-test. Replaces the 7-step manual bench doc
      // (hardware/TESTING.md) with one re-runnable, serial-driven routine.
      runSelfTest();
    }
  }
}

// ---------- Guided commissioning self-test [C7] ----------
// One routine the builder runs right after assembly. It sequences the three
// bench checks the old hardware/TESTING.md walked by hand — motors, dispenser,
// sensors — and, crucially, turns the two "physically redo the assembly" fixes
// into persisted software fixes: a wrong-way wheel flips + saves an invert flag
// on the Uno (INVL/INVR), and a mis-seated flap is corrected with a saved trim
// (TRIM+/TRIM-). Entirely serial-driven, non-destructive, safe to re-run.
//
// This is a BLOCKING bench routine on purpose: the robot is not conditioning a
// dog during commissioning, so pausing the main loop to walk the operator through
// prompts is fine. Any motor drive it triggers is self-limiting (the Uno pulses
// for 500 ms, and its 1 s movement watchdog is a backstop). lastTick/lastSample
// are reset on exit so the paused time can't spike dt or the sample cadence.
void runSelfTest() {
  Serial.println("\n=== [C7] CANIS guided self-test (bench) ===");
  Serial.println("Put the wheels OFF THE GROUND. Answer the prompts over Serial.");

  selfTestMotor("MOTL", "LEFT",  "INVL");   // (a) motors, one wheel at a time
  selfTestMotor("MOTR", "RIGHT", "INVR");
  selfTestDispenser();                       // (b) dispenser flap + trim
  selfTestSensors();                         // (c) live sensor readings

  Serial.println("=== self-test complete (re-run 'c' any time) ===\n");
  lastTick   = millis();   // don't let the paused time balloon dt / sampling
  lastSample = millis();
}

// (a) Pulse one motor, ask if it spun the EXPECTED way; on 'n' flip + persist that
// motor's invert flag on the Uno and re-pulse so the fix is verified on the spot.
void selfTestMotor(const char* pulseCmd, const char* name, const char* invertCmd) {
  Serial.print("\n[C7] MOTOR test — pulsing "); Serial.print(name); Serial.println(" wheel forward...");
  Serial2.println(pulseCmd);
  delay(700);   // let the operator watch the ~500 ms Uno pulse
  char ans = promptChar("     did it spin the EXPECTED (forward) direction? [y/n]", "yn");
  if (ans == 'n') {
    Serial2.println(invertCmd);   // Uno toggles + saves the invert flag
    Serial.print("     -> flipped + saved "); Serial.print(name);
    Serial.println(" invert on the Uno. Re-pulsing to verify...");
    delay(150);
    Serial2.println(pulseCmd);
    delay(700);
    Serial.println("     (calibration saved either way — persists across reboots)");
  } else {
    Serial.print("     "); Serial.print(name); Serial.println(" direction OK.");
  }
}

// (b) Cycle the dispenser; if the flap looked wrong, offer +/- trim nudges that
// re-fire so the operator sees each adjustment, until they accept.
void selfTestDispenser() {
  Serial.println("\n[C7] DISPENSER test — cycling the flap open/close...");
  Serial2.println("DISPENSE 0");   // id 0 = test-fire, not a tracked reward
  delay(600);
  char ans = promptChar("     did the flap open then close cleanly? [y/n]", "yn");
  if (ans == 'y') { Serial.println("     dispenser flap OK."); return; }
  Serial.println("     Adjust the flap zero:  [+]=open a bit more  [-]=close a bit more  [y]=accept");
  while (true) {
    char k = promptChar("     trim [+/-/y]", "+-y");
    if (k == 'y') { Serial.println("     dispenser trim accepted (saved on the Uno)."); return; }
    Serial2.println(k == '+' ? "TRIM+" : "TRIM-");   // Uno clamps +/-15, saves, re-seats
    delay(200);
    Serial2.println("DISPENSE 0");                    // re-fire so the change is visible
    delay(600);
  }
}

// (c) Live sensor readings for a few seconds, PASS/FAIL vs sane thresholds, using
// the SAME confirmed-read logic as the firmware. CRITICAL: it reads PIR_PIN in the
// INPUT_PULLDOWN mode setup() already configured — it does NOT re-pinMode it as
// INPUT. That fixes the Sensors_Test.ino divergence (TECH_DEBT #5) where the
// standalone test's floating INPUT could show phantom motion the brain never sees:
// here the real firmware's pin mode is the single source of truth.
void selfTestSensors() {
  Serial.println("\n[C7] SENSOR test — reading for ~4s. Wave a hand at each ultrasonic and the PIR.");
  Serial.println("     cols:  L(cm) C(cm) R(cm) | zones(LCR) | PIR");
  Confirmed stPir;
  Confirmed stZone[3];
  bool pirEverHigh = false;
  bool usEverValid[3] = { false, false, false };

  unsigned long start = millis();
  unsigned long lastRow = 0;
  while (millis() - start < 4000) {
    if (millis() - lastRow < CANIS_SAMPLE_MS) continue;
    lastRow = millis();

    bool pirRaw = digitalRead(PIR_PIN) == HIGH;   // [C7] pin is already INPUT_PULLDOWN
    if (pirRaw) pirEverHigh = true;
    stPir.update(pirRaw);

    Serial.print("     ");
    for (int i = 0; i < NUM_US; i++) {
      float d = readDistanceCm(US_TRIG[i], US_ECHO[i]);
      bool valid = (d > 0 && d < 400);          // real echo in a plausible range
      if (valid) usEverValid[i] = true;
      stZone[i].update(d > 0 && d <= CANIS_ZONE_CM);
      if (d > 0) Serial.print(d, 1); else Serial.print("---");
      Serial.print(" ");
      delay(5);
    }
    Serial.print("| ");
    for (int i = 0; i < NUM_US; i++) Serial.print(stZone[i].state ? "Y" : ".");
    Serial.print("  | ");
    Serial.println(stPir.state ? "MOTION" : ".");
  }

  Serial.println("     --- sensor results ---");
  for (int i = 0; i < NUM_US; i++) {
    Serial.print("     ultrasonic "); Serial.print(US_DIR[i]); Serial.print(": ");
    Serial.println(usEverValid[i] ? "PASS (got valid echoes)"
                                   : "FAIL (no valid echo — check wiring / 1k series resistor)");
  }
  Serial.print("     PIR: ");
  Serial.println(pirEverHigh ? "PASS (saw motion)"
                             : "CHECK (no motion seen — wave again, or allow the 60s warm-up)");
}

// [C7] Blocking prompt: flush stray input, print the question, and busy-wait for
// one of validChars (case-insensitive). Fine for a bench routine — the loop is
// intentionally paused while the operator answers.
char promptChar(const char* prompt, const char* validChars) {
  while (Serial.available()) Serial.read();   // drop anything already buffered
  Serial.println(prompt);
  while (true) {
    if (Serial.available()) {
      char c = tolower(Serial.read());
      if (c && strchr(validChars, c)) return c;
    }
    delay(5);
  }
}

// ---------- Real sensors ----------
void readSensors(unsigned long now) {
  if (now - lastSample < CANIS_SAMPLE_MS) return;
  lastSample = now;

  bool warm = (now - bootTime) >= CANIS_WARMUP_MS;

  bool pirRaw = digitalRead(PIR_PIN) == HIGH;
  if (!warm) pirRaw = false;
  if (pir.update(pirRaw))
    Serial.printf("  [SENSOR] PIR  -> %s\n",  pir.state  ? "MOTION"  : "clear");

  bool anyZone = false;
  for (int i = 0; i < NUM_US; i++) {
    float dist = readDistanceCm(US_TRIG[i], US_ECHO[i]);
    usCm[i] = dist;
    bool inZoneRaw = (dist > 0 && dist <= CANIS_ZONE_CM);
    if (usZone[i].update(inZoneRaw))
      Serial.printf("  [SENSOR] ZONE %-7s -> %s\n",
                    US_DIR[i], usZone[i].state ? "IN ZONE" : "clear");
    if (usZone[i].state) anyZone = true;
    delay(5);
  }
  zoneActive = anyZone;
}

float readDistanceCm(int trig, int echo) {
  digitalWrite(trig, LOW);  delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10);
  digitalWrite(trig, LOW);
  unsigned long dur = pulseIn(echo, HIGH, CANIS_ECHO_TIMEOUT_US);
  if (dur == 0) return -1.0;
  return dur * 0.0343 / 2.0;
}

const char* localBearing() {
  int best = -1; float bestCm = 1e9;
  for (int i = 0; i < NUM_US; i++) {
    if (usZone[i].state && usCm[i] > 0 && usCm[i] < bestCm) { bestCm = usCm[i]; best = i; }
  }
  return best >= 0 ? US_DIR[best] : nullptr;
}

// ---------- Arousal ----------
// [OPT-4] Clamp dt. A blocking HTTP call or WiFi stall can make one loop's dt
// balloon to seconds; with RISE=30/s that alone would jump arousal past
// CORRECTION (70) in a single tick, firing a phantom intervention. Capping dt
// bounds the per-tick change to (RISE-DECAY)*DT_MAX.
void updateArousal(float dt) {
  if (dt > CANIS_DT_MAX_S) dt = CANIS_DT_MAX_S;
  detectionActive = pir.state || zoneActive || visionActive;
  if (detectionActive) arousal += CANIS_AROUSAL_RISE_PER_SEC * dt;
  arousal -= CANIS_DECAY_PER_SEC * dt;
  if (arousal > CANIS_AROUSAL_MAX) arousal = CANIS_AROUSAL_MAX;
  if (arousal < 0)                 arousal = 0;
}

// ---------- State machine with hysteresis [OPT-3] ----------
// The original used single thresholds (>=70 CORRECTION, >=20 AROUSAL), so a dog
// sitting right at a boundary flapped the state every frame and re-fired
// interventions/rewards. This is a Schmitt trigger: you must climb past ENTER
// to raise a state, and fall below EXIT to lower it, giving a stable dead-band.
State classify(State current, float a) {
  switch (current) {
    case CALM:
      if (a >= CANIS_CORRECTION_ENTER) return CORRECTION;
      if (a >= CANIS_AROUSAL_ENTER)    return AROUSAL;
      return CALM;
    case AROUSAL:
      if (a >= CANIS_CORRECTION_ENTER) return CORRECTION;
      if (a <  CANIS_AROUSAL_EXIT)     return CALM;
      return AROUSAL;
    case CORRECTION:
      if (a <  CANIS_AROUSAL_EXIT)      return CALM;      // dropped all the way out
      if (a <  CANIS_CORRECTION_EXIT)   return AROUSAL;   // eased off, still watching
      return CORRECTION;
  }
  return CALM;
}

void updateState() {
  prevState = state;
  state = classify(prevState, arousal);

  if (state != prevState) {
    Serial.print(">>> STATE: ");
    Serial.print(name(prevState)); Serial.print(" -> "); Serial.println(name(state));

    if (state == CORRECTION) interventionCount++;

    if (prevState == CORRECTION && state != CORRECTION) stopMotion(millis());

    if (state == CALM && (prevState == AROUSAL || prevState == CORRECTION)) {
      reward("settled / left zone");
      lastCalmReward = millis();
    }
    if (state == CALM) calmSince = millis();

    lastReport = 0;
  }
}

void runStateBehavior(unsigned long now) {
  switch (state) {
    case CALM:
      if (now - bootTime >= CANIS_WARMUP_MS &&
          now - calmSince >= CANIS_CALM_REWARD_MS && now - lastCalmReward >= CANIS_CALM_REWARD_MS) {
        reward("sustained calm");
        lastCalmReward = now;
      }
      break;
    case AROUSAL:
      break;
    case CORRECTION:
      if (now - lastCorrectionAction >= CANIS_CORRECTION_ACTION_MS) {
        lastCorrectionAction = now;
#if CANIS_OBSERVE_ONLY
        // [B5] Observation-only: this is exactly the false-positive event the
        // baseline probe measures — log it, but fire no buzzer and no motion.
        Serial.println("  [OBSERVE] correction WOULD fire (buzzer + orient suppressed)");
#else
        tone(BUZZER_PIN, CANIS_CORRECTION_TONE_FREQ, CANIS_CORRECTION_TONE_MS);
        Serial.print("  [OUTPUT] correction cue (buzzer)");
        if (EXPOSURE_LEVEL >= 1) {
          const char* dir = localBearing();
          if (!dir) dir = zoneToDir(visionZone);
          // [OPT-8][SAFETY] Level-1 welfare invariant: "orient/pivot ONLY, NEVER
          // approach." The bearing can legitimately be "FORWARD" — localBearing()
          // returns it when the CENTRE ultrasonic is nearest, and zoneToDir()
          // returns it as its default — which the Arduino executes as a straight
          // forward drive AT the dog. A forward approach is only permitted at
          // EXPOSURE_LEVEL >= 2 (enforced again in serviceMotion's phase-2 gate).
          // Below that, rewrite a FORWARD bearing into a token in-place pivot (a
          // short LEFT nudge) so we still reorient toward the zone without ever
          // closing distance on the dog. Levels >= 2 are unchanged.
          if (EXPOSURE_LEVEL < 2 && strcmp(dir, "FORWARD") == 0) {
            dir = "LEFT";   // hold ground: pivot in place, do NOT approach
          }
          startMotion(dir, CANIS_ORIENT_MS, now);
          motionPhase = 1;
          Serial.print(" + orient "); Serial.print(dir);
        } else {
          Serial.print(" + (movement off, exposure 0)");
        }
        if (visionZone[0]) { Serial.print(" zone="); Serial.print(visionZone); }
        Serial.println();
#endif  // !CANIS_OBSERVE_ONLY
      }
      break;
  }
}

// ---------- Movement ----------
void startMotion(const char* cmd, unsigned long durMs, unsigned long now) {
  strncpy(moveCmd, cmd, sizeof(moveCmd) - 1);
  moveCmd[sizeof(moveCmd) - 1] = '\0';
  moveUntil     = now + durMs;
  lastMovePulse = now;
  Serial2.println(moveCmd);
}

void stopMotion(unsigned long now) {
  (void)now;
  if (moveCmd[0]) Serial2.println("STOP");
  moveCmd[0]  = '\0';
  moveUntil   = 0;
  motionPhase = 0;
}

void serviceMotion(unsigned long now) {
  if (!moveCmd[0]) return;

  if ((long)(now - moveUntil) >= 0) {
    if (motionPhase == 1 && EXPOSURE_LEVEL >= 2 && state == CORRECTION) {
      startMotion("FORWARD", CANIS_APPROACH_MS, now);
      motionPhase = 2;
    } else {
      stopMotion(now);
    }
    return;
  }

  if (now - lastMovePulse >= CANIS_MOVE_PULSE_MS) {
    Serial2.println(moveCmd);
    lastMovePulse = now;
  }
}

bool labelHas(const char* hay, const char* needleLower) {
  for (; *hay; hay++) {
    const char* h = hay; const char* n = needleLower;
    while (*n && tolower((unsigned char)*h) == *n) { h++; n++; }
    if (!*n) return true;
  }
  return false;
}

const char* zoneToDir(const char* z) {
  if (labelHas(z, "left"))  return "LEFT";
  if (labelHas(z, "right")) return "RIGHT";
  return "FORWARD";
}

// ---------- Reward ----------
void reward(const char* why) {
  unsigned long now = millis();

#if CANIS_OBSERVE_ONLY
  // [B5] Observation-only build: log the treat we WOULD have given (kept in the
  // record for the baseline probe) but dispense nothing.
  Serial.print("  [OBSERVE] reward WOULD fire (dispense suppressed) -- ");
  Serial.println(why);
  return;
#endif

  // [OPT-11] A faulted dispenser must go SILENT, not keep firing markers with
  // no treat behind them (an unpaired marker erodes the conditioned signal).
  if (dispenserFaulted) {
    Serial.print("  [OUTPUT] reward held (DISPENSER FAULTED — press 'd' after fixing) -- ");
    Serial.println(why);
    return;
  }

  if (lastRewardMs != 0 && now - lastRewardMs < CANIS_REWARD_COOLDOWN_MS) {
    Serial.print("  [OUTPUT] reward held (cooldown) -- "); Serial.println(why);
    return;
  }

#if CANIS_USE_DISPENSE_ACK
  // [OPT-7] Count on DELIVERY. Cap check counts an in-flight (unacked) treat so
  // we never overshoot the hopper cap while awaiting confirmation.
  if (rewardCount + (awaitingAck ? 1 : 0) >= CANIS_MAX_SESSION_REWARDS) {
    Serial.println("  [OUTPUT] reward held (session cap reached)");
    return;
  }
  lastRewardMs = now;              // cooldown starts at intent (throttles re-sends)
  awaitingAck  = true;
  outstandingReqId = ++reqIdSeq;   // [C5] correlate this dispense with its ACK/JAM (ids start at 1)
  ackDeadline  = now + CANIS_ACK_TIMEOUT_MS;
  ackLen       = 0;
  Serial2.print("DISPENSE "); Serial2.println(outstandingReqId);
  Serial.print("  [OUTPUT] DISPENSE #"); Serial.print(outstandingReqId);
  Serial.print(" sent, awaiting ACK -- "); Serial.println(why);
#else
  if (rewardCount >= CANIS_MAX_SESSION_REWARDS) {
    Serial.println("  [OUTPUT] reward held (session cap reached)");
    return;
  }
  lastRewardMs = now;
  rewardCount++;
  Serial2.println("DISPENSE");
  Serial.print("  [OUTPUT] >>> REWARD (sent DISPENSE) -- "); Serial.println(why);
#endif
}

#if CANIS_USE_DISPENSE_ACK
// [OPT-7] Watch Serial2 for the Arduino's "ACK" after a completed drop. Only
// then does reward_count climb, so the dashboard number reflects treats the dog
// actually got, not commands we merely sent (a jam/empty hopper no longer
// inflates the count). A missed ACK is logged and the slot freed.
void serviceAck(unsigned long now) {
  while (Serial2.available()) {
    char c = Serial2.read();
    if (c == '\n' || c == '\r') {
      ackBuf[ackLen] = '\0';
      if (ackLen > 0) {
        // [C5] Parse "ACK <id>" / "JAM <id>". atoi of the tail yields the echoed
        // correlation id (a bare "ACK"/"JAM" or a sentinel "ACK 0" parses to 0).
        bool isAck = strncmp(ackBuf, "ACK", 3) == 0;
        bool isJam = strncmp(ackBuf, "JAM", 3) == 0;
        if (isAck || isJam) {
          int rid = atoi(ackBuf + 3);
          if (awaitingAck && rid == outstandingReqId) {
            if (isAck) {
              rewardCount++;
              consecJams = 0;   // [OPT-11] a confirmed drop resets the jam streak
              Serial.print("  [OUTPUT] >>> REWARD confirmed (ACK #");
              Serial.print(rid); Serial.print("). count="); Serial.println(rewardCount);
            } else {
              // [OPT-11] The Arduino's drop sensor saw no treat fall (even after a
              // retry). Do not count; escalate to FAULTED on repeated jams.
              jamCount++;
              consecJams++;
              Serial.print("  [OUTPUT] !! DISPENSER JAM reported (#");
              Serial.print(rid); Serial.print(", consecutive: ");
              Serial.print(consecJams); Serial.println(")");
              if (consecJams >= CANIS_JAM_FAULT_THRESHOLD) {
                dispenserFaulted = true;
                Serial.println("  [OUTPUT] !!! DISPENSER FAULTED — auto rewards suspended. Fix hopper, then press 'd'.");
              }
            }
            awaitingAck = false;
            outstandingReqId = 0;
            lastReport  = 0;   // reflect the delivered treat / jam immediately
          } else {
            // [C5] No matching outstanding request — e.g. an "ACK 0" from a local
            // button-press dispense landing inside our wait window, or a late
            // reply from an already-timed-out request. Discard it: crediting it
            // to our pending reward is exactly the ±1 miscount this id prevents.
            Serial.print("  [OUTPUT] ignored dispense reply '"); Serial.print(ackBuf);
            Serial.print("' (awaiting=");
            if (awaitingAck) Serial.print(outstandingReqId); else Serial.print("none");
            Serial.println(")");
          }
        }
      }
      ackLen = 0;
    } else if (ackLen < (int)sizeof(ackBuf) - 1) {
      ackBuf[ackLen++] = c;
    }
  }
  if (awaitingAck && (long)(now - ackDeadline) >= 0) {
    awaitingAck = false;
    outstandingReqId = 0;
    Serial.println("  [OUTPUT] !! dispense ACK timeout (drop not confirmed) — not counted");
  }
}
#endif

// ---------- Vision latch TTL [OPT-9] ----------
// pollCommand() refreshes lastVisionRefresh on every successful poll that sees
// the dog. If the Pi/WiFi dies while visionActive is set, no poll can ever
// clear it — the original would hold arousal up forever. Expire it locally.
void serviceVisionTtl(unsigned long now) {
  if (visionActive && now - lastVisionRefresh > CANIS_VISION_TTL_MS) {
    visionActive = false;
    visionZone[0] = '\0';
    lastReport = 0;
    Serial.println("  [VISION] latch expired (no poll refresh) -> zone clear");
  }
}

void logStatus(unsigned long now) {
  if (now - lastLog >= 1000) {
    lastLog = now;
    Serial.print("["); Serial.print(name(state));
    Serial.print("] arousal="); Serial.print(arousal, 1);
    Serial.print(detectionActive ? "  (detecting)" : "");
    Serial.println();
  }
}

// ---------- Push state to the Pi ----------
unsigned long postBackoffUntil = 0;

void reportToPi(unsigned long now) {
  if (moveCmd[0]) return;   // [OPT-10] never block HTTP while feeding the motion watchdog
  if ((long)(now - postBackoffUntil) < 0) return;
  if (now - lastReport < CANIS_REPORT_MS) return;
  lastReport = now;
  if (WiFi.status() != WL_CONNECTED) return;

  char body[256];
  snprintf(body, sizeof(body),
    "{\"state\":\"%s\",\"arousal\":%.1f,\"arousal_max\":%.1f,"
    "\"intervention_count\":%d,\"reward_count\":%d,\"jam_count\":%d,"
    "\"exposure_level\":%d,\"uptime_sec\":%lu}",
    name(state), arousal, CANIS_AROUSAL_MAX,
    interventionCount, rewardCount, jamCount,
    EXPOSURE_LEVEL, (unsigned long)(now / 1000));

  char url[64];
  snprintf(url, sizeof(url), "http://%s:%d/ingest", PI_HOST, PI_PORT);

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  if (CANIS_TOKEN[0]) http.addHeader("X-CANIS-Token", CANIS_TOKEN);  // [OPT-2] auth
  http.setTimeout(CANIS_HTTP_TIMEOUT_MS);
  int code = http.POST((uint8_t*)body, strlen(body));
  if (code <= 0) {
    Serial.printf("  [NET] POST failed (%s) — backing off %lums\n",
                  http.errorToString(code).c_str(), CANIS_POST_BACKOFF_MS);
    postBackoffUntil = now + CANIS_POST_BACKOFF_MS;
  } else if (code == 401 || code == 403) {
    Serial.printf("  [NET] POST rejected (%d) — check CANIS token matches the Pi\n", code);
  }
  http.end();
}

// ---------- Announce live config to the Pi [C1] ----------
// Kills the last config-drift class (ENGINEERING_REVIEW §6.3): rather than the
// dashboard GUESSING firmware tunables from a hand-copied canis_config.py, the
// ESP32 POSTs the values it is ACTUALLY running to /config once per link (at
// boot, and again after wifiWatchdog clears the latch on a reconnect). The
// dashboard then displays firmware truth; canis_config.py is sim-fallback only.
void postConfig(unsigned long now) {
  if (configPosted) return;
  if (moveCmd[0]) return;                 // [OPT-10] never block HTTP mid-motion
  if ((long)(now - postBackoffUntil) < 0) return;
  if (WiFi.status() != WL_CONNECTED) return;

  char body[512];
  snprintf(body, sizeof(body),
    "{\"fw\":\"CANIS_Core-opt\",\"observe_only\":%d,"
    "\"exposure_level\":%d,\"exposure_default\":%d,"
    "\"arousal_max\":%.1f,\"decay_per_sec\":%.1f,\"rise_per_sec\":%.1f,\"dt_max_s\":%.2f,"
    "\"arousal_enter\":%d,\"arousal_exit\":%d,\"correction_enter\":%d,\"correction_exit\":%d,"
    "\"reward_cooldown_ms\":%lu,\"calm_reward_ms\":%lu,\"max_session_rewards\":%d,"
    "\"vision_ttl_ms\":%lu,\"jam_fault_threshold\":%d,"
    "\"use_dispense_ack\":%d,\"ack_timeout_ms\":%lu}",
    (int)CANIS_OBSERVE_ONLY,
    EXPOSURE_LEVEL, (int)CANIS_EXPOSURE_DEFAULT,
    CANIS_AROUSAL_MAX, CANIS_DECAY_PER_SEC, CANIS_AROUSAL_RISE_PER_SEC, CANIS_DT_MAX_S,
    (int)CANIS_AROUSAL_ENTER, (int)CANIS_AROUSAL_EXIT,
    (int)CANIS_CORRECTION_ENTER, (int)CANIS_CORRECTION_EXIT,
    (unsigned long)CANIS_REWARD_COOLDOWN_MS, (unsigned long)CANIS_CALM_REWARD_MS,
    (int)CANIS_MAX_SESSION_REWARDS,
    (unsigned long)CANIS_VISION_TTL_MS, (int)CANIS_JAM_FAULT_THRESHOLD,
    (int)CANIS_USE_DISPENSE_ACK, (unsigned long)CANIS_ACK_TIMEOUT_MS);

  char url[64];
  snprintf(url, sizeof(url), "http://%s:%d/config", PI_HOST, PI_PORT);

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  if (CANIS_TOKEN[0]) http.addHeader("X-CANIS-Token", CANIS_TOKEN);  // [OPT-2] auth
  http.setTimeout(CANIS_HTTP_TIMEOUT_MS);
  int code = http.POST((uint8_t*)body, strlen(body));
  if (code == 200) {
    configPosted = true;
    Serial.println("  [NET] posted live config to /config");
  } else if (code == 401 || code == 403) {
    // Auth failure won't fix itself without a reflash; latch so we don't spin.
    Serial.printf("  [NET] /config rejected (%d) — check CANIS token matches the Pi\n", code);
    configPosted = true;
  } else if (code <= 0) {
    postBackoffUntil = now + CANIS_POST_BACKOFF_MS;   // Pi down -> retry after backoff
  }
  http.end();
}

// ---------- Poll the Pi for vision commands ----------
void pollCommand(unsigned long now) {
  if (moveCmd[0]) return;   // [OPT-10] never block HTTP while feeding the motion watchdog
  if ((long)(now - postBackoffUntil) < 0) return;
  if (now - lastPoll < CANIS_POLL_MS) return;
  lastPoll = now;
  if (WiFi.status() != WL_CONNECTED) return;

  char url[96];
  // [OPT-2] token as query param on the GET (mutating detect->command channel).
  if (CANIS_TOKEN[0])
    snprintf(url, sizeof(url), "http://%s:%d/command?token=%s", PI_HOST, PI_PORT, CANIS_TOKEN);
  else
    snprintf(url, sizeof(url), "http://%s:%d/command", PI_HOST, PI_PORT);

  HTTPClient http;
  http.begin(url);
  http.setTimeout(CANIS_HTTP_TIMEOUT_MS);
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    body.trim();
    bool wasActive = visionActive;
    if (body == "NONE" || body.length() == 0) {
      visionActive = false;
      visionZone[0] = '\0';
    } else {
      visionActive = true;
      lastVisionRefresh = now;   // [OPT-9] a live sighting refreshes the latch TTL
      strncpy(visionZone, body.c_str(), sizeof(visionZone) - 1);
      visionZone[sizeof(visionZone) - 1] = '\0';
    }
    if (visionActive != wasActive) {
      if (visionActive) { Serial.print("  [VISION] dog in zone: "); Serial.println(visionZone); }
      else              { Serial.println("  [VISION] zone clear"); }
      lastReport = 0;
    }
  } else if (code <= 0) {
    postBackoffUntil = now + CANIS_POST_BACKOFF_MS;
  }
  http.end();
}

const char* name(State s) {
  switch (s) { case CALM: return "CALM"; case AROUSAL: return "AROUSAL"; case CORRECTION: return "CORRECTION"; }
  return "?";
}
