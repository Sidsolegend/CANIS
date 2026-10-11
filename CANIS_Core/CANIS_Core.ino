/*
  CANIS - Core (ESP32)  [OPTIMIZED DUPLICATE]
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
    [OPT-5] Non-blocking WiFi reconnect watchdog in loop() - a post-boot drop no
            longer disables reportToPi/pollCommand forever. (orig had connect-once
            only, connectWiFi CANIS_Core.ino:200)
    [OPT-7] Optional dispense-ACK: reward_count increments only when the Arduino
            confirms a completed drop, so the count reflects DELIVERY not intent.
            (orig reward() counted on send, CANIS_Core.ino:445-446)
    [C5]    Dispense correlation id. Each reward
            DISPENSE carries an incrementing id ("DISPENSE <n>"); the Arduino
            echoes it ("ACK <n>" / "JAM <n>") and serviceAck() only counts a
            reply whose id matches the outstanding request. A reply with a stale
            or sentinel id - e.g. "ACK 0" from a local button-press dispense that
            lands inside our wait window - is logged and DISCARDED, not counted,
            fixing the documented ±1 miscount. Manual 'd' test-fires as id 0.
    [OPT-9] Vision-latch TTL: visionActive now expires locally if no successful
            /command poll refreshes it within CANIS_VISION_TTL_MS. The original
            could hold arousal up FOREVER if WiFi/the Pi died while the latch
            was set - a permanent phantom CORRECTION (buzzer + motion at
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
            dispenser FAULTED and suppresses further treat requests - a marker
            tone with no treat actively damages the conditioning. Manual 'd'
            clears the fault. jam_count is reported to the dashboard.
    [B5]    Observation-only build. When the
            compile flag is 1 the full sense->arousal->state->report pipeline
            runs untouched but EVERY actuator is gated off (no treat, no buzzer,
            no motion). Replaces the "unplug the link / empty the hopper" hack
            the baseline week used for its overnight false-positive probe: the
            state trace is still logged, so you measure what the robot WOULD have
            done with zero risk of a stray cue reaching the dog.
    [C1]    Runtime-config anti-drift. At boot (and after a WiFi
            reconnect) the ESP32 POSTs the tunables it is ACTUALLY running to the
            dashboard's /config route, so the dashboard shows firmware truth
            instead of a hand-copied canis_config.py guess (which becomes the
            sim/offline fallback only). Kills the last drift class (§6.3).
    [C4]    Defensive exposure change. The ORIGINAL cycled
            0->1->2->0 on a single 'm' press, so a stray double-press from 0
            landed on 2 (approach) unconfirmed. Now 'm' only ARMS the next level
            and a distinct 'y' commits it, within CANIS_EXPOSURE_CONFIRM_MS; an
            un-confirmed arm auto-expires so it can't be committed later.
    [MOB]   Mobile drivetrain + CBF safe motion. Wire formats are LAW per
            docs/INTERFACE_CONTRACT.md; rationale per docs/SAFE_MOTION_CBF_SPEC.md.
            Compile-gated by CANIS_MOBILE_ENABLED (default 0 = stationary: no
            /motion poll, no /telemetry stream, no "VEL" verb, encoder pins
            untouched - wire traffic byte-identical to the pre-upgrade build).
            When 1: quadrature encoders are counted in hardware on the ESP32
            PCNT peripheral, odometry (v, omega) + IMU heading + VL53LX ToF
            range stream to POST /telemetry at CANIS_ODOM_STREAM_HZ (10 Hz),
            GET /motion "MOVE <v> <omega>"/"STOP" is polled at 10 Hz, clamped
            (speed caps, never-approach floor, 600 ms freshness TTL), mapped to
            per-wheel targets and closed by a 20 Hz per-wheel velocity PI into
            "VEL <pwmL> <pwmR>" on the existing Serial2 grammar. Legacy
            open-loop word-motion is suppressed in mobile builds; every
            existing fail-safe (exposure gate, CANIS_NEVER_APPROACH, the Uno's
            1000 ms watchdog, reboot -> exposure 0) stays supreme, and any
            sensor/link failure degrades ONE-WAY to STOP/stationary.

  NOTE: no arduino_secrets.h value is reproduced here; the include and the
  SECRET_* names are the same public interface as the original.

  Serial Monitor @ 115200.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <ctype.h>             // tolower() for zone-label matching
#include <stdlib.h>            // atoi() for parsing the ACK/JAM correlation id [C5]
#include "canis_config.h"      // [OPT-1] single source of truth (symlink -> ../canis_config.h)
#include "arduino_secrets.h"   // WiFi/Pi creds - gitignored, see arduino_secrets.h.example

#if CANIS_MOBILE_ENABLED
// [MOB] ESP32 PCNT peripheral - hardware quadrature counting, zero CPU cost
// per edge (WHY the encoders live on the ESP32 and not the Uno, see the pin
// budget note in canis_config.h). This is the legacy IDF driver header: it
// ships with arduino-esp32 core 2.x and is still present (deprecated) on
// core 3.x; if a future core drops it, migrate to driver/pulse_cnt.h.
#include "driver/pcnt.h"
#endif

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
const int PIR_PIN  = -1;   // PIR REMOVED (redundant with the camera); -1 = no PIR, reads as "clear".
const int   US_TRIG[3] = { 32, 27, 14 };          // LEFT, CENTRE, RIGHT   CENTRE trig is on 27 (as wired); 26 is reserved for left-encoder B
const int   US_ECHO[3] = { 34, 25, 35 };          // LEFT, CENTRE, RIGHT
const char* US_DIR[3]  = { "LEFT", "FORWARD", "RIGHT" };
const int   NUM_US = CANIS_NUM_US;
// Index 1 is the CENTRE SR04M-2; it can be left unwired (CANIS_US_CENTRE_ENABLED 0).
// (A macro, not a function: a function here would become the first definition in the file and the
// Arduino auto-prototypes would be inserted above `enum State`, breaking the build.)
#define usEnabled(i) (!((i) == 1 && !CANIS_US_CENTRE_ENABLED))

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
const int BUZZER_PIN = -1;   // ESP32 correction-cue buzzer NOT wired (GUARD-mode only);
                             // GPIO 33 now belongs to the left encoder. The treat marker tone plays on
                             // the Uno's pin 6. Set back to 33 (and move the encoder) to re-enable.

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
#define CANIS_US_HUNG (-2.0f)            // readDistanceCm(): ECHO stuck HIGH (sensor hung); distinct from -1 = no echo
bool          usFault[3]      = { false, false, false };   // true = sensor hung; reads as no data, roam treats it as BLOCKED
uint8_t       usHungRuns[3]   = { 0, 0, 0 };               // consecutive stuck reads
unsigned long usFaultSince[3] = { 0, 0, 0 };               // when the current wait before a retry began
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
unsigned long lastVisionRefresh = 0;
#if CANIS_BARK_REWARD_ENABLED
unsigned long quietSeq = 0;          // last X-CANIS-Quiet-Seq value handled
bool          quietSeqSynced = false; // false until the first poll after boot sets the baseline
#endif   // [OPT-9] last successful /command poll that saw the dog

// ---------- Movement sub-behavior ----------
char          moveCmd[12]    = "";
unsigned long moveUntil      = 0;
unsigned long lastMovePulse  = 0;
int           motionPhase    = 0;

// ---------- Roaming / patrol [ROAM] ----------
// patrolMode = currently wandering (vs engaged/stationary). lastEngageEnd marks
// when the last dog engagement ended, so roaming waits CANIS_ROAM_RESUME_MS
// before starting up again (don't lurch off the instant the dog settles).
bool          patrolMode     = false;
unsigned long lastEngageEnd  = 0;

// GUARD (correct at a spot) / PLAY (roam + reward-chase + flee) / PLACE (reward
// settling on his mat). Toggle 'g' / 'p' / 'b'.
int           robotMode      = CANIS_MODE_DEFAULT;

// PLACE mode bookkeeping: when Shifu was first seen continuously on his mat, and
// when he was last rewarded for staying there.
unsigned long placeSince      = 0;
unsigned long lastPlaceReward = 0;

int EXPOSURE_LEVEL = CANIS_EXPOSURE_DEFAULT;

// ---------- Exposure two-step ARM->CONFIRM [C4] ----------
// 'm' proposes the next level here (does NOT change the live EXPOSURE_LEVEL);
// 'y' commits it iff still within CANIS_EXPOSURE_CONFIRM_MS. -1 = nothing armed.
int           proposedExposure = -1;
unsigned long exposureArmedAt  = 0;

// ---------- WiFi reconnect watchdog [OPT-5] ----------
unsigned long lastWifiCheck = 0;
unsigned long postBackoffUntil = 0;   // shared HTTP backoff; declared up here because the mobile code (pollMotion, postTelemetry) uses it before reportToPi() is defined

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

#if CANIS_MOBILE_ENABLED
// ---------- Mobile drivetrain / CBF safe motion [MOB] ----------
// Wire formats are LAW per docs/INTERFACE_CONTRACT.md; rationale per
// docs/SAFE_MOTION_CBF_SPEC.md. This whole block (and every [MOB] function
// below) compiles away at CANIS_MOBILE_ENABLED=0, so the stationary build's
// behavior and wire traffic are unchanged.
//
// PCNT UNIT ASSIGNMENT (hardware assumption): LEFT encoder = PCNT_UNIT_0,
// RIGHT encoder = PCNT_UNIT_1; units 2..7 stay free and nothing else in this
// sketch touches PCNT. Each unit decodes BOTH quadrature channels (full 4x),
// which is why the 4x-edge figure CANIS_ENC_COUNTS_PER_REV=1320 is consumed
// as-is. The hardware counter is only 16-bit, so limit events at +/-ENC_H_LIM
// fold into a 64-bit accumulator in an ISR - enc_l/enc_r are therefore
// cumulative, SIGNED, wrap-safe, and never reset mid-session (contract §1.1).
const int16_t ENC_H_LIM = 30000;    // fold point, comfortably inside int16
volatile int64_t encAccumL = 0;     // ISR-owned overflow accumulators; every
volatile int64_t encAccumR = 0;     //   access goes through encMux
portMUX_TYPE encMux = portMUX_INITIALIZER_UNLOCKED;

// Derived drivetrain geometry (compile-time float math from canis_config.h).
const float MOB_M_PER_COUNT = (CANIS_WHEEL_DIAMETER_MM / 1000.0f) * PI
                              / (float)CANIS_ENC_COUNTS_PER_REV;   // meters per 4x count
const float MOB_TRACK_M     = CANIS_WHEEL_BASE_MM / 1000.0f;       // track width, m

// Per-wheel velocity PI gains + feedforward. BRING-UP TUNABLES: they live
// here (not canis_config.h) until they've been measured against the real
// drivetrain, then graduate to the single source of truth. KFF assumes the
// wheel does ~0.5 m/s at full 255 duty on the bench - MEASURE that first;
// a wrong KFF makes the PI fight the feedforward instead of trimming it.
const float MOB_KFF        = 255.0f / 0.50f;  // PWM per m/s of target (feedforward)
const float MOB_KP         = 300.0f;          // PWM per m/s of velocity error
const float MOB_KI         = 600.0f;          // PWM per (m/s)*s of integrated error
const float MOB_PI_INT_MAX = 100.0f;          // integrator clamp, PWM units (anti-windup)
const float MOB_STOP_EPS   = 0.005f;          // |v| AND |omega| below this = stop target

// Live mobile state (contract field names in comments).
int64_t encL = 0, encR = 0;            // latest cumulative counts (enc_l / enc_r)
int64_t prevEncL = 0, prevEncR = 0;    // previous control-tick sample (odometry deltas)
float   odomV     = 0.0f;              // body linear velocity, m/s (+ = forward)
float   odomOmega = 0.0f;              // body angular velocity, rad/s (+ = CCW / left)
float   headingDeg = 0.0f;             // [0, 360), CLOCKWISE-positive from boot (§1.1)
float   tofM = -1.0f;                  // VL53LX range, m; -1.0 = the ONLY no-data sentinel
unsigned long odomTsMs = 0;            // millis() of the odometry sample (telemetry ts_ms)

float   cmdV = 0.0f, cmdOmega = 0.0f;  // last clamped MOVE target from /motion
unsigned long lastMotionOkMs = 0;      // millis() of the last good MOVE (0 = never)
bool    mobileDown = false;            // latched: /motion or /telemetry 404/401 -> stationary

unsigned long lastControlMs    = 0;    // 20 Hz wheel loop scheduler
unsigned long lastMotionPollMs = 0;    // 10 Hz /motion poll scheduler
unsigned long lastTelemetryMs  = 0;    // 10 Hz /telemetry stream scheduler
float   piIntL = 0.0f, piIntR = 0.0f;  // PI integrators (already in PWM units)
int     lastPwmL = 0, lastPwmR = 0;    // last transmitted VEL pair
bool    wheelsMoving = false;          // a VEL stream is active (explicit STOP not yet sent)
unsigned long lastVelTx = 0;           // last VEL transmit (500 ms watchdog keepalive)
#endif  // CANIS_MOBILE_ENABLED

void setup() {
  Serial.begin(115200);
  delay(300);
  if (PIR_PIN >= 0) pinMode(PIR_PIN, INPUT_PULLDOWN);
  for (int i = 0; i < NUM_US; i++) {
    pinMode(US_TRIG[i], OUTPUT);
    pinMode(US_ECHO[i], INPUT);
    digitalWrite(US_TRIG[i], LOW);
  }
  if (BUZZER_PIN >= 0) pinMode(BUZZER_PIN, OUTPUT);

  Serial2.begin(9600, SERIAL_8N1, ARD_RX_PIN, ARD_TX_PIN);

#if CANIS_MOBILE_ENABLED
  mobileSetup();          // [MOB] PCNT quadrature units + overflow ISR
#endif

  Serial.println("\nCANIS core online. PIR warming up (~60s).");
  Serial.println("Keys:  t=trigger  r=reset  d=dispense  m=arm exposure  y=confirm exposure  f=test fwd  s=stop  u=show ultrasonics  c=self-test  g=GUARD  p=PLAY  b=PLACE");
#if CANIS_OBSERVE_ONLY
  Serial.println("*** OBSERVE-ONLY BUILD [B5]: sensing + reporting only - NO treat/buzzer/motion ***");
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
#if CANIS_MOBILE_ENABLED
  pollMotion(now);        // [MOB] GET /motion @ 10 Hz -> clamped (v, omega) target
  mobileControl(now);     // [MOB] 20 Hz odometry + fail-safe stack + wheel PI -> VEL/STOP
  streamTelemetry(now);   // [MOB] POST /telemetry @ 10 Hz (OPT-10-exempt, freshest-only)
#endif
#if CANIS_ROAM_ENABLED
  serviceRoam(now);       // [ROAM/PLAY] wander + reward-chase + flee while in PLAY mode
#endif
  servicePlace(now);      // [PLACE] reward Shifu for settling on his mat
#if CANIS_USE_DISPENSE_ACK
  serviceAck(now);        // [OPT-7] count rewards only on confirmed delivery
#endif
  logStatus(now);
  reportToPi(now);

  delay(20);
}

// ---------- WiFi ----------
// Say WHY the link drops. Reason codes are ESP-IDF wifi_err_reason_t values.
const char* wifiReasonName(int r) {
  switch (r) {
    case 1:   return "UNSPECIFIED";
    case 2:   return "AUTH_EXPIRE";
    case 3:   return "AUTH_LEAVE";
    case 4:   return "ASSOC_EXPIRE";
    case 8:   return "ASSOC_LEAVE (AP kicked us)";
    case 15:  return "4WAY_HANDSHAKE_TIMEOUT (wrong password or weak signal)";
    case 200: return "BEACON_TIMEOUT (lost the AP: weak signal)";
    case 201: return "NO_AP_FOUND (SSID not seen: wrong name, 5 GHz-only, or out of range)";
    case 202: return "AUTH_FAIL (wrong password?)";
    case 203: return "ASSOC_FAIL";
    case 204: return "HANDSHAKE_TIMEOUT";
    case 205: return "CONNECTION_FAIL";
    default:  return "other";
  }
}
volatile unsigned long wifiAssocMs = 0;   // millis() when we last associated with the AP and were waiting for an IP; 0 = not waiting
void onWifiEvent(arduino_event_id_t event, arduino_event_info_t info) {
  static unsigned long lastLogMs = 0;
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED: {
      unsigned long n = millis();
      if (n - lastLogMs > 1500) {   // the stack can retry many times a second; do not flood the log
        lastLogMs = n;
        int r = info.wifi_sta_disconnected.reason;
        Serial.printf("  [NET] disconnected, reason=%d %s\n", r, wifiReasonName(r));
      }
      break;
    }
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      wifiAssocMs = millis();
      Serial.printf("  [NET] associated with the AP, channel %d, signal %d dBm; waiting for an IP\n",
                    info.wifi_sta_connected.channel, WiFi.RSSI());
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      wifiAssocMs = 0;
      Serial.printf("  [NET] got an IP, signal %d dBm (about -50 is strong, -70 is weak, -80 is unusable)\n", WiFi.RSSI());
      break;
    default: break;
  }
}

void connectWiFi() {
  WiFi.onEvent(onWifiEvent);
  Serial.print("WiFi: connecting to "); Serial.println(WIFI_SSID);
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.setSleep(false);
  if (CANIS_WIFI_STATIC_IP[0]) {   // fixed IP: skips DHCP entirely
    IPAddress ip, gw, sn;
    if (ip.fromString(CANIS_WIFI_STATIC_IP) && gw.fromString(CANIS_WIFI_GATEWAY) && sn.fromString(CANIS_WIFI_SUBNET)) {
      WiFi.config(ip, gw, sn, gw);   // DNS = the router
      Serial.printf("WiFi: using fixed IP %s (DHCP skipped)\n", CANIS_WIFI_STATIC_IP);
    } else {
      Serial.println("WiFi: bad fixed-IP settings in canis_config.h, falling back to DHCP");
    }
  }
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(300); Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("\nWiFi connected. ESP32 IP: "); Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi FAILED - running offline (Serial only). Check SSID/PASS.");
  }
}

// [OPT-5] Non-blocking reconnect watchdog. The original connected once in
// setup(); if the AP dropped after boot, reportToPi()/pollCommand() were dead
// forever. This periodically checks the link and kicks a non-blocking
// WiFi.reconnect() - no busy-wait, so the control loop never stalls.
void wifiWatchdog(unsigned long now) {
  if (now - lastWifiCheck < CANIS_WIFI_CHECK_MS) return;
  lastWifiCheck = now;
  if (WiFi.status() != WL_CONNECTED) {
    // Associated but no IP yet: DHCP can take a while on a weak link, and reconnect() would restart it.
    if (wifiAssocMs != 0 && millis() - wifiAssocMs < CANIS_WIFI_DHCP_WAIT_MS) {
      Serial.printf("  [NET] associated, still waiting for an IP (%lu s)\n", (millis() - wifiAssocMs) / 1000UL);
      return;
    }
    // status codes: 1 NO_SSID_AVAIL (AP not seen), 4 CONNECT_FAILED (auth/assoc), 5 CONNECTION_LOST, 6 DISCONNECTED
    static unsigned long lastKick = 0;
    if (lastKick == 0 || now - lastKick >= CANIS_WIFI_RETRY_MS) {
      lastKick = now;
      Serial.printf("  [NET] WiFi down (status=%d) -> reconnect()\n", (int)WiFi.status());
      WiFi.reconnect();   // returns immediately; status flips when it succeeds
    } else {
      Serial.printf("  [NET] WiFi still down (status=%d), waiting for the join to finish\n", (int)WiFi.status());
    }
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
      // [C4] ARM the next exposure level - does NOT change the live level yet.
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
        Serial.println("  [MANUAL] 'y' ignored - no armed exposure change (arm with 'm' first)");
      }
      proposedExposure = -1;
    } else if (c == 'f') {
      startMotion("FORWARD", 1500, millis());
      motionPhase = 0;
      Serial.println("  [MANUAL] test FORWARD (1.5s)");
    } else if (c == 'u') {
      // read-only: what each ultrasonic currently reads (-1 = no echo / skipped), and
      // whether it counts as "in zone". For aiming sensors and finding one that sees the chassis/wheel.
      Serial.print("  [SENSORS] cm:");
      for (int i = 0; i < NUM_US; i++) {
        Serial.print("  "); Serial.print(US_DIR[i]); Serial.print("=");
        if (usFault[i]) Serial.print("HUNG");
        else if (usCm[i] > 0) Serial.print(usCm[i], 1); else Serial.print("none");
        Serial.print(usZone[i].state ? "(IN ZONE)" : "");
        if (usEnabled(i) && digitalRead(US_ECHO[i]) == HIGH) Serial.print("[echo HIGH]");
      }
      Serial.print("   zone limit="); Serial.print((float)CANIS_ZONE_CM, 0); Serial.print(" cm");
      if (WiFi.status() == WL_CONNECTED) { Serial.print("   WiFi OK "); Serial.print(WiFi.RSSI()); Serial.println(" dBm"); }
      else { Serial.print("   WiFi DOWN (status="); Serial.print((int)WiFi.status()); Serial.println(")"); }
    } else if (c == 's') {
      stopMotion(millis());
      Serial.println("  [MANUAL] STOP");
    } else if (c == 'c') {
      // [C7] Guided commissioning self-test. Replaces the 7-step manual bench doc
      // (hardware/TESTING.md) with one re-runnable, serial-driven routine.
      runSelfTest();
    } else if (c == 'g') {
      robotMode = CANIS_MODE_GUARD;
      stopMotion(millis());
      patrolMode = false;
      Serial.println("  [MODE] GUARD - stationary sentry (buzzer correct + reward on leave)");
    } else if (c == 'p') {
      robotMode = CANIS_MODE_PLAY;
      Serial.println("  [MODE] PLAY - roam + reward-chase + flee (never toward the dog)");
    } else if (c == 'b') {
      robotMode  = CANIS_MODE_PLACE;
      stopMotion(millis());
      patrolMode = false;
      placeSince = 0;
      Serial.println("  [MODE] PLACE - point me at his bed; I treat him for settling there");
    }
  }
}

// ---------- Guided commissioning self-test [C7] ----------
// One routine the builder runs right after assembly. It sequences the three
// bench checks the old hardware/TESTING.md walked by hand - motors, dispenser,
// sensors - and, crucially, turns the two "physically redo the assembly" fixes
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
  Serial.print("\n[C7] MOTOR test - pulsing "); Serial.print(name); Serial.println(" wheel forward...");
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
    Serial.println("     (calibration saved either way - persists across reboots)");
  } else {
    Serial.print("     "); Serial.print(name); Serial.println(" direction OK.");
  }
}

// (b) Cycle the dispenser; if the flap looked wrong, offer +/- trim nudges that
// re-fire so the operator sees each adjustment, until they accept.
void selfTestDispenser() {
  Serial.println("\n[C7] DISPENSER test - cycling the flap open/close...");
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
// INPUT_PULLDOWN mode setup() already configured - it does NOT re-pinMode it as
// INPUT. That fixes the Sensors_Test.ino divergence where the
// standalone test's floating INPUT could show phantom motion the brain never sees:
// here the real firmware's pin mode is the single source of truth.
void selfTestSensors() {
  Serial.println("\n[C7] SENSOR test - reading for ~4s. Wave a hand at each ultrasonic and the PIR.");
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

    bool pirRaw = (PIR_PIN >= 0) && digitalRead(PIR_PIN) == HIGH;   // [C7] pin is already INPUT_PULLDOWN
    if (pirRaw) pirEverHigh = true;
    stPir.update(pirRaw);

    Serial.print("     ");
    for (int i = 0; i < NUM_US; i++) {
      float d = usEnabled(i) ? readDistanceCm(US_TRIG[i], US_ECHO[i]) : -1.0f;
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
    Serial.println(!usEnabled(i) ? "SKIPPED (disabled in canis_config.h)"
                   : usEverValid[i] ? "PASS (got valid echoes)"
                                    : "FAIL (no valid echo - check wiring / 1k series resistor)");
  }
  Serial.print("     PIR: ");
  Serial.println(pirEverHigh ? "PASS (saw motion)"
                             : "CHECK (no motion seen - wave again, or allow the 60s warm-up)");
}

// [C7] Blocking prompt: flush stray input, print the question, and busy-wait for
// one of validChars (case-insensitive). Fine for a bench routine - the loop is
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

  bool pirRaw = (PIR_PIN >= 0) && digitalRead(PIR_PIN) == HIGH;
  if (!warm) pirRaw = false;
  if (pir.update(pirRaw))
    Serial.printf("  [SENSOR] PIR  -> %s\n",  pir.state  ? "MOTION"  : "clear");

  bool anyZone = false;
  for (int i = 0; i < NUM_US; i++) {
    float dist = -1.0f;
    if (!usEnabled(i)) {
      // skipped by config
    } else if (usFault[i] && now - usFaultSince[i] < CANIS_US_FAULT_RETRY_MS) {
      // faulted: parked until the next retry, so a dead sensor does not slow every cycle
    } else {
      dist = readDistanceCm(US_TRIG[i], US_ECHO[i]);
      if (dist == CANIS_US_HUNG) {
        dist = -1.0f;
        if (usHungRuns[i] < 255) usHungRuns[i]++;
        if (usHungRuns[i] >= CANIS_US_HUNG_RUNS) {
          if (!usFault[i])
            Serial.printf("  [SENSOR] %s is HUNG (echo stuck high): treated as BLOCKED. Power-cycle the robot to free it.\n", US_DIR[i]);
          usFault[i] = true;
          usFaultSince[i] = now;            // wait before the next retry
        }
      } else {
        if (usFault[i]) Serial.printf("  [SENSOR] %s recovered\n", US_DIR[i]);
        usFault[i] = false;
        usHungRuns[i] = 0;
      }
    }
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
  // ECHO idles LOW and a valid pulse is <= ~40 ms. Still HIGH after HUNG_WAIT_US: the sensor is stuck.
  if (digitalRead(echo) == HIGH) {
    unsigned long w = micros();
    while (digitalRead(echo) == HIGH && micros() - w < CANIS_US_HUNG_WAIT_US) { }
    if (digitalRead(echo) == HIGH) return CANIS_US_HUNG;
  }
  digitalWrite(trig, LOW);  delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(CANIS_TRIG_PULSE_US);
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
#if CANIS_ROAM_ENABLED
  // [ROAM] While patrolling, ONLY the camera marker counts as "the dog".
  // Ultrasonics (walls) and PIR (the robot's own motion) would otherwise raise
  // arousal at every obstacle and fire phantom corrections. Once engaged (and
  // therefore stationary again) the full sensor set is meaningful, so restore it.
  detectionActive = patrolMode ? visionActive
                               : (pir.state || zoneActive || visionActive);
#else
  detectionActive = pir.state || zoneActive || visionActive;
#endif
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
      // Reward LEAVING only when GUARDing a spot. In PLACE we must NOT reward him
      // for leaving his mat (that's the opposite of the goal); PLAY does its own.
      if (robotMode == CANIS_MODE_GUARD) {
        reward("settled / left zone");
        lastCalmReward = millis();
      }
      lastEngageEnd = millis();   // [ROAM] start the resume-delay before wandering again
    }
    if (state == CALM) calmSince = millis();

    lastReport = 0;
  }
}

void runStateBehavior(unsigned long now) {
  switch (state) {
    case CALM:
      // Sustained-calm treats belong to GUARD mode (reward the dog for being calm
      // in the guarded area). PLAY rewards chasing, PLACE rewards settling - each
      // does its own, so don't dump calm treats into an empty room here.
      if (robotMode != CANIS_MODE_GUARD) break;
#if CANIS_CALM_PRESENCE_WINDOW_MS > 0
      // no calm treats unless the camera has actually seen him recently.
      if (lastVisionRefresh == 0 || now - lastVisionRefresh > CANIS_CALM_PRESENCE_WINDOW_MS) break;
#endif
      if (now - bootTime >= CANIS_WARMUP_MS &&
          now - calmSince >= CANIS_CALM_REWARD_MS && now - lastCalmReward >= CANIS_CALM_REWARD_MS) {
        reward("sustained calm");
        lastCalmReward = now;
      }
      break;
    case AROUSAL:
      break;
    case CORRECTION:
      // Only GUARD mode scolds. PLAY handles the dog via serviceRoam (treat +
      // flee) and PLACE rewards him for being there - neither buzzes at him.
      if (robotMode != CANIS_MODE_GUARD) break;
      if (now - lastCorrectionAction >= CANIS_CORRECTION_ACTION_MS) {
        lastCorrectionAction = now;
#if CANIS_OBSERVE_ONLY
        // [B5] Observation-only: this is exactly the false-positive event the
        // baseline probe measures - log it, but fire no buzzer and no motion.
        Serial.println("  [OBSERVE] correction WOULD fire (buzzer + orient suppressed)");
#else
        if (BUZZER_PIN >= 0) tone(BUZZER_PIN, CANIS_CORRECTION_TONE_FREQ, CANIS_CORRECTION_TONE_MS);
        else Serial2.printf("BEEP %d %d\n", (int)CANIS_CORRECTION_TONE_FREQ, (int)CANIS_CORRECTION_TONE_MS);   // shared buzzer on the Uno's pin 6
        Serial.print("  [OUTPUT] correction cue (buzzer)");
#if CANIS_NEVER_APPROACH
        // HARD INVARIANT: never move toward the dog. Guard correction is the
        // buzzer disengage cue only - the robot holds its ground (or, if you want
        // it to retreat, that's the PLAY-mode flee). No orient/approach here.
        Serial.print(" + (hold - never toward the dog)");
#else
        if (EXPOSURE_LEVEL >= 1) {
          const char* dir = localBearing();
          if (!dir) dir = zoneToDir(visionZone);
          if (EXPOSURE_LEVEL < 2 && strcmp(dir, "FORWARD") == 0) {
            dir = "LEFT";   // hold ground: pivot in place, do NOT approach
          }
          startMotion(dir, CANIS_ORIENT_MS, now);
          motionPhase = 1;
          Serial.print(" + orient "); Serial.print(dir);
        } else {
          Serial.print(" + (movement off, exposure 0)");
        }
#endif  // CANIS_NEVER_APPROACH
        if (visionZone[0]) { Serial.print(" zone="); Serial.print(visionZone); }
        Serial.println();
#endif  // !CANIS_OBSERVE_ONLY
      }
      break;
  }
}

// ---------- Movement ----------
void startMotion(const char* cmd, unsigned long durMs, unsigned long now) {
#if CANIS_MOBILE_ENABLED
  // [MOB] In mobile builds locomotion comes ONLY from the CBF pipeline
  // (/motion -> clamps -> wheel PI -> VEL); the legacy open-loop words would
  // fight the velocity loop on the same Serial2 link. Contract §4: the CBF
  // pipeline "replac[es] the legacy open-loop roam word-commands as the
  // source of locomotion." moveCmd stays empty, so the [OPT-10] HTTP deferral
  // never engages from legacy motion either.
  (void)durMs; (void)now;
  Serial.print("  [MOB] legacy motion '"); Serial.print(cmd);
  Serial.println("' suppressed (CANIS_MOBILE_ENABLED build)");
#else
  strncpy(moveCmd, cmd, sizeof(moveCmd) - 1);
  moveCmd[sizeof(moveCmd) - 1] = '\0';
  moveUntil     = now + durMs;
  lastMovePulse = now;
  Serial2.println(moveCmd);
#endif
}

void stopMotion(unsigned long now) {
  (void)now;
#if CANIS_MOBILE_ENABLED
  // [MOB] A LOCAL stop request (manual 's', state transitions, mode switches)
  // also drops the CBF target: send the explicit STOP verb immediately and
  // invalidate the last MOVE, so the wheel loop holds STOP until the Pi
  // serves a FRESH command (<= 100 ms later if it genuinely wants motion).
  cmdV = 0.0f;
  cmdOmega = 0.0f;
  lastMotionOkMs = 0;
  if (wheelsMoving) {
    Serial2.println("STOP");
    wheelsMoving = false;
  }
#endif
  if (moveCmd[0]) Serial2.println("STOP");
  moveCmd[0]  = '\0';
  moveUntil   = 0;
  motionPhase = 0;
}

void serviceMotion(unsigned long now) {
  if (!moveCmd[0]) return;

  if ((long)(now - moveUntil) >= 0) {
#if !CANIS_NEVER_APPROACH
    if (motionPhase == 1 && EXPOSURE_LEVEL >= 2 && state == CORRECTION) {
      startMotion("FORWARD", CANIS_APPROACH_MS, now);   // (disabled by CANIS_NEVER_APPROACH)
      motionPhase = 2;
    } else
#endif
    {
      stopMotion(now);
    }
    return;
  }

  if (now - lastMovePulse >= CANIS_MOVE_PULSE_MS) {
    Serial2.println(moveCmd);
    lastMovePulse = now;
  }
}

#if CANIS_MOBILE_ENABLED
// ================= Mobile drivetrain / CBF safe motion [MOB] =================
// Encoders (PCNT hardware) -> odometry -> fail-safe stack -> per-wheel
// velocity PI -> "VEL <pwmL> <pwmR>" on the existing Serial2 grammar, plus
// the /motion poll and /telemetry stream. The Uno stays the dumb, certifiable
// executor: its 1000 ms MOTION_TIMEOUT_MS watchdog is UNCHANGED and remains
// the supreme fail-safe underneath everything in this section.

// One PCNT unit per wheel, BOTH channels used for full 4x quadrature decode:
// channel 0 counts A edges with B as the direction line, channel 1 counts B
// edges with A as the direction line (the standard PCNT rotary-encoder
// configuration), so all four edges of a line pair count.
//
// SIGN CONVENTION (hardware assumption - verify at bring-up): channel A must
// LEAD channel B when that wheel drives the robot FORWARD, giving POSITIVE
// counts. If a side reads backwards on the bench, swap that side's A/B pins
// (or their #defines in canis_config.h) - do NOT patch the sign in software
// here, or the odometry and the Uno's PWM sign will disagree.
void setupEncoder(int unitNum, int pinA, int pinB) {
  pcnt_unit_t unit = (pcnt_unit_t)unitNum;

  // Open-collector encoder outputs need these pulls; push-pull outputs ignore
  // them. GPIO16-19 all have internal pulls - the WROVER remap to input-only
  // 36/39 (see the pin-budget note in canis_config.h) does NOT: add external
  // pull-ups there.
  pinMode(pinA, INPUT_PULLUP);
  pinMode(pinB, INPUT_PULLUP);

  pcnt_config_t cfg = {};
  cfg.unit          = unit;
  cfg.counter_h_lim = ENC_H_LIM;     // hardware resets to 0 at the limit and
  cfg.counter_l_lim = -ENC_H_LIM;    //   fires the event the ISR accumulates

  cfg.channel        = PCNT_CHANNEL_0;    // A edges, B = direction
  cfg.pulse_gpio_num = pinA;
  cfg.ctrl_gpio_num  = pinB;
  cfg.pos_mode       = PCNT_COUNT_INC;    // A rising ...
  cfg.neg_mode       = PCNT_COUNT_DEC;    // A falling ...
  cfg.lctrl_mode     = PCNT_MODE_KEEP;    // ... counted as-is while B is low
  cfg.hctrl_mode     = PCNT_MODE_REVERSE; // ... reversed while B is high
  pcnt_unit_config(&cfg);

  cfg.channel        = PCNT_CHANNEL_1;    // B edges, A = direction (opposite sense)
  cfg.pulse_gpio_num = pinB;
  cfg.ctrl_gpio_num  = pinA;
  cfg.pos_mode       = PCNT_COUNT_DEC;
  cfg.neg_mode       = PCNT_COUNT_INC;
  cfg.lctrl_mode     = PCNT_MODE_KEEP;
  cfg.hctrl_mode     = PCNT_MODE_REVERSE;
  pcnt_unit_config(&cfg);

  // Glitch filter: 1023 APB cycles (~12.8 us @ 80 MHz) rejects contact/EMI
  // spikes but passes real edges by a wide margin - the fastest legitimate
  // edge at the 0.35 m/s cap is ~2.3 kHz per channel (~430 us apart).
  pcnt_set_filter_value(unit, 1023);
  pcnt_filter_enable(unit);

  pcnt_event_enable(unit, PCNT_EVT_H_LIM);
  pcnt_event_enable(unit, PCNT_EVT_L_LIM);
  pcnt_counter_pause(unit);
  pcnt_counter_clear(unit);
  // resumed in mobileSetup() AFTER the ISR is attached, so no event is lost
}

// Limit-event ISR: the 16-bit hardware counter reset to 0 at +/-ENC_H_LIM;
// fold that excursion into the 64-bit accumulator. Installed via the default
// (non-IRAM-flag) PCNT ISR service, so calling driver code here is legal.
static void IRAM_ATTR pcntLimitIsr(void* arg) {
  int unitNum = (int)(intptr_t)arg;
  uint32_t status = 0;
  pcnt_get_event_status((pcnt_unit_t)unitNum, &status);
  volatile int64_t* accum = (unitNum == (int)PCNT_UNIT_0) ? &encAccumL : &encAccumR;
  portENTER_CRITICAL_ISR(&encMux);
  if (status & PCNT_EVT_H_LIM) *accum += ENC_H_LIM;
  if (status & PCNT_EVT_L_LIM) *accum -= ENC_H_LIM;
  portEXIT_CRITICAL_ISR(&encMux);
}

// Wrap-safe cumulative signed count: 64-bit accumulator + live 16-bit hardware
// count, read atomically against the limit ISR (a fold between the two reads
// would otherwise show a transient +/-ENC_H_LIM glitch to the odometry).
int64_t encoderCount(int unitNum, volatile int64_t* accum) {
  int16_t hw = 0;
  portENTER_CRITICAL(&encMux);
  pcnt_get_counter_value((pcnt_unit_t)unitNum, &hw);
  int64_t total = *accum + hw;
  portEXIT_CRITICAL(&encMux);
  return total;
}

void mobileSetup() {
  setupEncoder(PCNT_UNIT_0, CANIS_ENC_L_A_PIN, CANIS_ENC_L_B_PIN);   // LEFT  -> unit 0
  setupEncoder(PCNT_UNIT_1, CANIS_ENC_R_A_PIN, CANIS_ENC_R_B_PIN);   // RIGHT -> unit 1
  pcnt_isr_service_install(0);   // default flags (not ESP_INTR_FLAG_IRAM)
  pcnt_isr_handler_add(PCNT_UNIT_0, pcntLimitIsr, (void*)(intptr_t)PCNT_UNIT_0);
  pcnt_isr_handler_add(PCNT_UNIT_1, pcntLimitIsr, (void*)(intptr_t)PCNT_UNIT_1);
  pcnt_counter_resume(PCNT_UNIT_0);
  pcnt_counter_resume(PCNT_UNIT_1);
  Serial.println("[MOB] mobile drivetrain armed: PCNT units 0/1 (L/R encoders), "
                 "/motion poll + /telemetry stream @10Hz, wheel PI @20Hz");
}

// VL53LX robot->dog range. The ToF driver lands on the RESERVED I2C pins
// (21/22, see canis_config.h pin budget) at bring-up; until it does, this
// returns the contract's ONLY no-data sentinel (-1.0, never a fake 0.0), and
// the safety clamp below treats "no range" as AT-THE-BOUNDARY - contract §4:
// no single sensor failure may produce a distance-reducing command. So a dog
// in view with no range data can never be approached.
float readTofM() {
  return -1.0f;   // TODO(bring-up): VL53LX ranging over I2C (GPIO 21/22)
}

// ---------- Poll the Pi for the CBF motion command [MOB] ----------
// GET /motion @ CANIS_ODOM_STREAM_HZ, mirroring /command's token-as-query
// style (contract §2.1). Deliberately NOT gated on moveCmd: legacy word-motion
// is suppressed in mobile builds (startMotion), and this poll IS the motion
// path, so the [OPT-10] deferral cannot apply to it.
void pollMotion(unsigned long now) {
  if (mobileDown) return;                        // one-way degrade to stationary
  if ((long)(now - postBackoffUntil) < 0) return;
  if (now - lastMotionPollMs < 1000UL / CANIS_ODOM_STREAM_HZ) return;
  lastMotionPollMs = now;
  if (WiFi.status() != WL_CONNECTED) return;     // TTL in mobileControl STOPs for us

  char url[96];
  if (CANIS_TOKEN[0])
    snprintf(url, sizeof(url), "http://%s:%d/motion?token=%s", PI_HOST, PI_PORT, CANIS_TOKEN);
  else
    snprintf(url, sizeof(url), "http://%s:%d/motion", PI_HOST, PI_PORT);

  HTTPClient http;
  http.begin(url);
  http.setTimeout(CANIS_HTTP_TIMEOUT_MS);
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    body.trim();
    float v = 0.0f, w = 0.0f;
    if (sscanf(body.c_str(), "MOVE %f %f", &v, &w) == 2) {
      // Defense in depth (contract §2.2 rule 1): clamp firmware-side ALWAYS
      // never trust the Pi to have clamped, even though it serves post-QP u*.
      if (v >  CANIS_MAX_LINEAR_MPS)  v =  CANIS_MAX_LINEAR_MPS;
      if (v < -CANIS_MAX_LINEAR_MPS)  v = -CANIS_MAX_LINEAR_MPS;
      if (w >  CANIS_MAX_ANGULAR_RPS) w =  CANIS_MAX_ANGULAR_RPS;
      if (w < -CANIS_MAX_ANGULAR_RPS) w = -CANIS_MAX_ANGULAR_RPS;
      cmdV = v; cmdOmega = w;
      lastMotionOkMs = now;          // the CANIS_MOTION_CMD_TTL_MS window starts here
    } else {
      // "STOP" - and anything unparseable degrades to the exact same thing.
      cmdV = 0.0f; cmdOmega = 0.0f;
      lastMotionOkMs = 0;            // 0 = no live MOVE -> control loop holds STOP
    }
  } else if (code == 404 || code == 401 || code == 403) {
    // Old dashboard (no /motion route) or a bad token: mobile is UNAVAILABLE
    // (contract §1.2/§4). Latch stationary for the session - neither cause
    // fixes itself without a redeploy/reflash, same rationale as postConfig's
    // auth latch. Degradation is one-way toward stationary.
    mobileDown = true;
    cmdV = 0.0f; cmdOmega = 0.0f;
    Serial.printf("  [MOB] /motion rejected (%d) - mobile UNAVAILABLE, latching stationary\n", code);
  } else if (code <= 0) {
    postBackoffUntil = now + CANIS_POST_BACKOFF_MS;   // Pi down -> shared backoff
  }
  http.end();
}

// One wheel's velocity PI + feedforward -> signed PWM in [-255, 255].
// integ is kept in PWM units so the clamp is a direct authority limit.
int wheelPi(float target, float meas, float& integ, float dt) {
  float err = target - meas;
  integ += MOB_KI * err * dt;
  if (integ >  MOB_PI_INT_MAX) integ =  MOB_PI_INT_MAX;   // anti-windup
  if (integ < -MOB_PI_INT_MAX) integ = -MOB_PI_INT_MAX;
  float u = MOB_KFF * target + MOB_KP * err + integ;
  if (u >  255.0f) u =  255.0f;
  if (u < -255.0f) u = -255.0f;
  return (int)lroundf(u);
}

// ---------- 20 Hz odometry + fail-safe stack + per-wheel velocity PI ----------
// Order inside one tick: sample encoders -> integrate odometry -> stack EVERY
// fail-safe onto the (v, omega) target -> differential map -> PI -> transmit
// "VEL <pwmL> <pwmR>" (or the explicit "STOP" verb).
void mobileControl(unsigned long now) {
  if (now - lastControlMs < 1000UL / CANIS_CONTROL_RATE_HZ) return;
  float dt = (now - lastControlMs) / 1000.0f;
  lastControlMs = now;
  if (dt > CANIS_DT_MAX_S) dt = CANIS_DT_MAX_S;   // same stall rationale as [OPT-4]

  // --- odometry from encoder deltas ---
  encL = encoderCount(PCNT_UNIT_0, &encAccumL);
  encR = encoderCount(PCNT_UNIT_1, &encAccumR);
  odomTsMs = now;                                  // contract: ts_ms = sample time
  float dL = (float)(encL - prevEncL) * MOB_M_PER_COUNT;
  float dR = (float)(encR - prevEncR) * MOB_M_PER_COUNT;
  prevEncL = encL;  prevEncR = encR;
  float measVL = dL / dt, measVR = dR / dt;        // per-wheel velocities, m/s
  odomV     = (measVL + measVR) * 0.5f;            // + = forward (contract §1.1)
  odomOmega = (measVR - measVL) / MOB_TRACK_M;     // + = CCW (right wheel faster)

  // Heading: encoder-integrated yaw standing in for the IMU (which lands on
  // the reserved I2C pins at bring-up). Contract §1.1 wants CLOCKWISE-positive
  // degrees in [0, 360) from the boot reference; odomOmega is CCW-positive,
  // hence the subtraction. NOTE: this drifts with wheel slip - the Pi's
  // estimator must treat it as a relative, drifting reference.
  headingDeg -= odomOmega * dt * (180.0f / PI);
  headingDeg = fmodf(headingDeg, 360.0f);
  if (headingDeg < 0.0f) headingDeg += 360.0f;

  tofM = readTofM();

  // --- fail-safe stack on the commanded (v, omega) ---
  float v = cmdV, w = cmdOmega;
  bool stopTarget = false;
  if (mobileDown) stopTarget = true;               // no mobile routes Pi-side
  if (lastMotionOkMs == 0 ||
      now - lastMotionOkMs > CANIS_MOTION_CMD_TTL_MS)
    stopTarget = true;                             // stale MOVE (>600 ms) -> STOP (§2.2 rule 3)
  if (EXPOSURE_LEVEL < 1) stopTarget = true;       // exposure gate OUTRANKS the CBF (§4):
                                                   //   level 0 = "no movement" stays law
#if CANIS_OBSERVE_ONLY
  stopTarget = true;                               // [B5] every actuator gated off
#endif

  // Never-approach floor (§2.2 rule 2): dog detected AND inside D_MIN - or
  // range UNKNOWN, because a ToF dropout must never enable a distance-reducing
  // command (§4) - clamps the forward component to <= 0. Pivoting in place
  // and backing away remain legal; CANIS_NEVER_APPROACH stays supreme (the
  // compile guard in canis_config.h enforces the pairing).
  bool dogSeen = detectionActive || visionActive;
  if (dogSeen && (tofM < 0.0f || tofM <= (float)CANIS_D_MIN_CM / 100.0f) && v > 0.0f)
    v = 0.0f;

  if (fabsf(v) < MOB_STOP_EPS && fabsf(w) < MOB_STOP_EPS) stopTarget = true;

  if (stopTarget) {
    // Stops are the EXPLICIT verb - NEVER "VEL 0 0" (contract §2.3) - so old
    // and new Uno firmware behave identically on a stop. Sent once per stop;
    // the Uno's own 1000 ms watchdog covers everything after that.
    if (wheelsMoving) {
      Serial2.println("STOP");
      wheelsMoving = false;
    }
    piIntL = piIntR = 0.0f;            // no integrator windup while parked
    lastPwmL = lastPwmR = 0;
    return;
  }

  // --- differential map + per-wheel PI -> signed PWM ---
  // v_L = v - omega*L/2, v_R = v + omega*L/2 (contract §2.2 rule 4; +omega =
  // CCW means the RIGHT wheel runs faster on a left turn).
  float vL = v - w * MOB_TRACK_M * 0.5f;
  float vR = v + w * MOB_TRACK_M * 0.5f;
  int pwmL = wheelPi(vL, measVL, piIntL, dt);
  int pwmR = wheelPi(vR, measVR, piIntR, dt);

  // PWM SIGN CONVENTION (contract §2.3): positive = that wheel FORWARD; the
  // Uno maps sign -> IN1..IN4 direction pins and magnitude -> ENA/ENB duty,
  // and does NO kinematics. Transmit on change at the control rate, and
  // re-send an unchanged pair every CANIS_MOVE_PULSE_MS (500 ms) as the
  // watchdog keepalive - exactly like the legacy motion words. Worst case
  // ~14 chars * 20 Hz = 280 B/s, comfortably inside the 9600-baud link.
  if (!wheelsMoving || pwmL != lastPwmL || pwmR != lastPwmR ||
      now - lastVelTx >= CANIS_MOVE_PULSE_MS) {
    Serial2.print("VEL ");
    Serial2.print(pwmL);
    Serial2.print(' ');
    Serial2.println(pwmR);
    lastVelTx = now;
    lastPwmL  = pwmL;
    lastPwmR  = pwmR;
    wheelsMoving = true;
  }
}

// ---------- Stream the motion frame to the Pi [MOB] ----------
// POST /telemetry @ CANIS_ODOM_STREAM_HZ (contract §1.2). EXEMPT from the
// [OPT-10] moveCmd deferral BY LAW - a moving robot is exactly when the Pi's
// estimator needs data (and moveCmd is always empty in mobile builds anyway).
// Freshest-only: a failed or missed frame is DROPPED, never queued; the next
// tick sends the newest sample instead.
void streamTelemetry(unsigned long now) {
  if (mobileDown) return;
  if ((long)(now - postBackoffUntil) < 0) return;
  if (now - lastTelemetryMs < 1000UL / CANIS_ODOM_STREAM_HZ) return;
  lastTelemetryMs = now;
  if (WiFi.status() != WL_CONNECTED) return;
  if (odomTsMs == 0) return;              // no odometry sample yet (first ticks)

  char body[224];
  snprintf(body, sizeof(body),
    "{\"ts_ms\":%lu,\"enc_l\":%lld,\"enc_r\":%lld,"
    "\"odom_v\":%.3f,\"odom_omega\":%.3f,\"tof_m\":%.3f,\"heading_deg\":%.1f}",
    (unsigned long)odomTsMs, (long long)encL, (long long)encR,
    odomV, odomOmega, tofM, headingDeg);

  char url[64];
  snprintf(url, sizeof(url), "http://%s:%d/telemetry", PI_HOST, PI_PORT);

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  if (CANIS_TOKEN[0]) http.addHeader("X-CANIS-Token", CANIS_TOKEN);  // [OPT-2] auth
  http.setTimeout(CANIS_HTTP_TIMEOUT_MS);
  int code = http.POST((uint8_t*)body, strlen(body));
  if (code == 404 || code == 401 || code == 403) {
    // Contract §1.2: 404/401 from /telemetry (old dashboard / bad token) means
    // mobile is UNAVAILABLE - STOP and behave stationary for the session.
    mobileDown = true;
    cmdV = 0.0f; cmdOmega = 0.0f;
    Serial.printf("  [MOB] /telemetry rejected (%d) - mobile UNAVAILABLE, latching stationary\n", code);
  } else if (code <= 0) {
    postBackoffUntil = now + CANIS_POST_BACKOFF_MS;   // dropped, not queued
  }
  http.end();
}
#endif  // CANIS_MOBILE_ENABLED

#if CANIS_ROAM_ENABLED
// Direction that moves AWAY from the dog - never toward him.
// At flee time Shifu is by definition in the FORWARD camera (that's what
// triggered PLAY), so "away" = backing/peeling off the front. Direction comes
// from the CAMERA only - where his marker sits in the frame (needs detect.py
// --directional for a left/right label; without it we just back straight off).
// The ultrasonics are NOT trusted for his bearing - they can't tell him from a
// wall; they only steer obstacle-avoidance while roaming.
const char* fleeDir() {
  const char* b = zoneToDir(visionZone);           // "LEFT" / "RIGHT" / "FORWARD"
  if (strcmp(b, "LEFT")  == 0) return "RIGHT";      // front-left  -> peel away to the right
  if (strcmp(b, "RIGHT") == 0) return "LEFT";       // front-right -> peel away to the left
  return "BACKWARD";                                // front-centre / no side -> back straight off
}

// ---------- PLAY engine + reactive roam [ROAM] ----------
// Runs LAST in the loop, after serviceMotion, so it only issues a new leg once
// the previous one has finished (moveCmd cleared). Only active in PLAY mode;
// GUARD mode is the stationary sentry and this halts any leftover motion.
//
// PLAY:
//   * "Shifu is here" = his ArUco MARKER is in the forward camera's view
//     (visionActive). Ultrasonics are NOT used to detect him - they can't tell
//     a dog from a wall - they only steer the roam and pick the flee direction.
//   * When his marker appears: drop a treat (reward() self-limits by cooldown +
//     daily cap) and FLEE (retreat leg via fleeDir) so he chases. Never toward.
//   * When no marker: wander, steering off obstacles (usCm, -1 = clear), so it
//     goes looking for him. The Arduino's 1 s watchdog is the motion backstop.
void serviceRoam(unsigned long now) {
  if (robotMode != CANIS_MODE_PLAY) {          // GUARD mode: no roam/play
    if (patrolMode) { stopMotion(now); patrolMode = false; }
    return;
  }

  // --- Shifu engaged (marker in view) -> reward + flee ---
  if (visionActive) {
    if (patrolMode) { patrolMode = false; Serial.println("  [PLAY] Shifu spotted - treat + flee"); }
    lastEngageEnd = now;                        // hold off roaming while he's around
    if (!moveCmd[0]) {                          // one action per finished leg
      if (lastRewardMs == 0 || now - lastRewardMs >= CANIS_REWARD_COOLDOWN_MS)
        reward("play - Shifu engaged");         // treat him for chasing (cooldown/cap gated)
      startMotion(fleeDir(), CANIS_PLAY_FLEE_MS, now);   // retreat: NEVER toward him
      motionPhase = 0;
    }
    return;
  }

  // --- no dog in view -> wander, looking for him ---
  bool wantPatrol = (now - bootTime      >= CANIS_WARMUP_MS)
                    && (now - lastEngageEnd >= CANIS_ROAM_RESUME_MS);
  if (!wantPatrol) {
    if (patrolMode) { stopMotion(now); patrolMode = false; }
    return;
  }
  if (!patrolMode) { patrolMode = true; Serial.println("  [PLAY] roaming - looking for Shifu"); }

  if (moveCmd[0]) return;                       // current leg still running

  float l = usCm[0], c = usCm[1], r = usCm[2];  // LEFT, CENTRE, RIGHT (-1 = clear)
  // A hung sensor is a blind spot, not "clear": count it as BLOCKED so the robot never drives on blind.
  bool cBlocked = usFault[1] || (c > 0 && c <= CANIS_ROAM_CLEAR_CM);
  bool lBlocked = usFault[0] || (l > 0 && l <= CANIS_ROAM_CLEAR_CM);
  bool rBlocked = usFault[2] || (r > 0 && r <= CANIS_ROAM_CLEAR_CM);

  if (!cBlocked && !lBlocked && !rBlocked) {
    startMotion("FORWARD", CANIS_ROAM_FWD_MS, now);
  } else {
    float lroom = usFault[0] ? 0.0f : ((l < 0) ? 1e9f : l);   // no echo == lots of room; a hung side == none
    float rroom = usFault[2] ? 0.0f : ((r < 0) ? 1e9f : r);
    startMotion(lroom >= rroom ? "LEFT" : "RIGHT", CANIS_ROAM_TURN_MS, now);
  }
  motionPhase = 0;
}
#endif  // CANIS_ROAM_ENABLED

// ---------- PLACE / "good boy" mode ----------
// Stationary: point the robot at Shifu's bed/mat. While his marker is in view
// (he's on his spot) it periodically drops a treat -> rewards him for STAYING
// there instead of getting into trouble (place-training - one of the most
// effective things for a bored/destructive dog). No buzzer, no motion. He must
// be there CANIS_PLACE_SETTLE_MS before the first treat (reward settling, not a
// pass-through), then a treat every CANIS_PLACE_REWARD_MS while he stays. If he
// leaves, the timer resets - he is never rewarded for leaving.
void servicePlace(unsigned long now) {
  if (robotMode != CANIS_MODE_PLACE) { placeSince = 0; return; }

  if (!visionActive) {                 // not on the mat -> reset, no reward
    if (placeSince) Serial.println("  [PLACE] Shifu left his spot");
    placeSince = 0;
    return;
  }

  if (placeSince == 0) {               // just arrived -> start the settle timer
    placeSince = now;
    Serial.println("  [PLACE] Shifu on his spot - settling...");
  }
  if (now - placeSince < CANIS_PLACE_SETTLE_MS) return;   // let him actually settle first

  if (lastPlaceReward == 0 || now - lastPlaceReward >= CANIS_PLACE_REWARD_MS) {
    reward("place - settled on his spot");   // treat for staying (cooldown/cap gated)
    lastPlaceReward = now;
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
    Serial.print("  [OUTPUT] reward held (DISPENSER FAULTED - press 'd' after fixing) -- ");
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
                Serial.println("  [OUTPUT] !!! DISPENSER FAULTED - auto rewards suspended. Fix hopper, then press 'd'.");
              }
            }
            awaitingAck = false;
            outstandingReqId = 0;
            lastReport  = 0;   // reflect the delivered treat / jam immediately
          } else {
            // [C5] No matching outstanding request - e.g. an "ACK 0" from a local
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
    Serial.println("  [OUTPUT] !! dispense ACK timeout (drop not confirmed) - not counted");
  }
}
#endif

// ---------- Vision latch TTL [OPT-9] ----------
// pollCommand() refreshes lastVisionRefresh on every successful poll that sees
// the dog. If the Pi/WiFi dies while visionActive is set, no poll can ever
// clear it - the original would hold arousal up forever. Expire it locally.
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

void reportToPi(unsigned long now) {
  if (moveCmd[0]) return;   // [OPT-10] never block HTTP while feeding the motion watchdog
  if ((long)(now - postBackoffUntil) < 0) return;
  if (now - lastReport < CANIS_REPORT_MS) return;
  lastReport = now;
  if (WiFi.status() != WL_CONNECTED) return;

#if CANIS_MOBILE_ENABLED
  // [MOB] Contract §1.1: mobile builds APPEND the seven mobile fields (exact
  // names) to the legacy 1 Hz report. Stationary builds take the #else branch
  // and omit them entirely, keeping that wire shape byte-identical.
  char body[384];
  snprintf(body, sizeof(body),
    "{\"state\":\"%s\",\"arousal\":%.1f,\"arousal_max\":%.1f,"
    "\"intervention_count\":%d,\"reward_count\":%d,\"jam_count\":%d,"
    "\"exposure_level\":%d,\"uptime_sec\":%lu,"
    "\"mobile\":1,\"enc_l\":%lld,\"enc_r\":%lld,"
    "\"odom_v\":%.3f,\"odom_omega\":%.3f,\"tof_m\":%.3f,\"heading_deg\":%.1f}",
    name(state), arousal, CANIS_AROUSAL_MAX,
    interventionCount, rewardCount, jamCount,
    EXPOSURE_LEVEL, (unsigned long)(now / 1000),
    (long long)encL, (long long)encR,
    odomV, odomOmega, tofM, headingDeg);
#else
  char body[256];
  snprintf(body, sizeof(body),
    "{\"state\":\"%s\",\"arousal\":%.1f,\"arousal_max\":%.1f,"
    "\"intervention_count\":%d,\"reward_count\":%d,\"jam_count\":%d,"
    "\"exposure_level\":%d,\"uptime_sec\":%lu}",
    name(state), arousal, CANIS_AROUSAL_MAX,
    interventionCount, rewardCount, jamCount,
    EXPOSURE_LEVEL, (unsigned long)(now / 1000));
#endif  // CANIS_MOBILE_ENABLED

  char url[64];
  snprintf(url, sizeof(url), "http://%s:%d/ingest", PI_HOST, PI_PORT);

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  if (CANIS_TOKEN[0]) http.addHeader("X-CANIS-Token", CANIS_TOKEN);  // [OPT-2] auth
  http.setTimeout(CANIS_HTTP_TIMEOUT_MS);
  int code = http.POST((uint8_t*)body, strlen(body));
  if (code <= 0) {
    Serial.printf("  [NET] POST failed (%s) - backing off %lums\n",
                  http.errorToString(code).c_str(), CANIS_POST_BACKOFF_MS);
    postBackoffUntil = now + CANIS_POST_BACKOFF_MS;
  } else if (code == 401 || code == 403) {
    Serial.printf("  [NET] POST rejected (%d) - check CANIS token matches the Pi\n", code);
  }
  http.end();
}

// ---------- Announce live config to the Pi [C1] ----------
// Kills the last config-drift class: rather than the
// dashboard GUESSING firmware tunables from a hand-copied canis_config.py, the
// ESP32 POSTs the values it is ACTUALLY running to /config once per link (at
// boot, and again after wifiWatchdog clears the latch on a reconnect). The
// dashboard then displays firmware truth; canis_config.py is sim-fallback only.
void postConfig(unsigned long now) {
  if (configPosted) return;
  if (moveCmd[0]) return;                 // [OPT-10] never block HTTP mid-motion
  if ((long)(now - postBackoffUntil) < 0) return;
  if (WiFi.status() != WL_CONNECTED) return;

  // [MOB] The nine contract §3.1 mobile keys are announced in BOTH builds, ON
  // PURPOSE (the one deliberate exception to "mobile compiles away"): the live
  // mobile_enabled value is exactly how the dashboard learns the drivetrain is
  // OFF, per contract §4 ("announced live via /config"). All nine names match
  // the dashboard's _CONFIG_NUM_KEYS whitelist (app.py); an older dashboard's
  // sanitizer simply drops unknown keys, so this is backward-safe.
  char body[768];
  snprintf(body, sizeof(body),
    "{\"fw\":\"CANIS_Core-opt\",\"observe_only\":%d,"
    "\"exposure_level\":%d,\"exposure_default\":%d,"
    "\"arousal_max\":%.1f,\"decay_per_sec\":%.1f,\"rise_per_sec\":%.1f,\"dt_max_s\":%.2f,"
    "\"arousal_enter\":%d,\"arousal_exit\":%d,\"correction_enter\":%d,\"correction_exit\":%d,"
    "\"reward_cooldown_ms\":%lu,\"calm_reward_ms\":%lu,\"max_session_rewards\":%d,"
    "\"vision_ttl_ms\":%lu,\"jam_fault_threshold\":%d,"
    "\"use_dispense_ack\":%d,\"ack_timeout_ms\":%lu,"
    "\"mobile_enabled\":%d,\"d_min_cm\":%d,\"d_max_cm\":%d,\"cbf_gamma\":%.2f,"
    "\"control_hz\":%d,\"odom_hz\":%d,\"motion_cmd_ttl_ms\":%lu,"
    "\"max_v_mps\":%.2f,\"max_w_rps\":%.2f}",
    (int)CANIS_OBSERVE_ONLY,
    EXPOSURE_LEVEL, (int)CANIS_EXPOSURE_DEFAULT,
    CANIS_AROUSAL_MAX, CANIS_DECAY_PER_SEC, CANIS_AROUSAL_RISE_PER_SEC, CANIS_DT_MAX_S,
    (int)CANIS_AROUSAL_ENTER, (int)CANIS_AROUSAL_EXIT,
    (int)CANIS_CORRECTION_ENTER, (int)CANIS_CORRECTION_EXIT,
    (unsigned long)CANIS_REWARD_COOLDOWN_MS, (unsigned long)CANIS_CALM_REWARD_MS,
    (int)CANIS_MAX_SESSION_REWARDS,
    (unsigned long)CANIS_VISION_TTL_MS, (int)CANIS_JAM_FAULT_THRESHOLD,
    (int)CANIS_USE_DISPENSE_ACK, (unsigned long)CANIS_ACK_TIMEOUT_MS,
    (int)CANIS_MOBILE_ENABLED, (int)CANIS_D_MIN_CM, (int)CANIS_D_MAX_CM, CANIS_CBF_GAMMA,
    (int)CANIS_CONTROL_RATE_HZ, (int)CANIS_ODOM_STREAM_HZ, (unsigned long)CANIS_MOTION_CMD_TTL_MS,
    CANIS_MAX_LINEAR_MPS, CANIS_MAX_ANGULAR_RPS);

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
    Serial.printf("  [NET] /config rejected (%d) - check CANIS token matches the Pi\n", code);
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
#if CANIS_BARK_REWARD_ENABLED
  static const char* hdrKeys[] = { "X-CANIS-Quiet-Seq", "X-CANIS-Quiet-Age" };
  http.collectHeaders(hdrKeys, 2);
#endif
  int code = http.GET();
  if (code == 200) {
#if CANIS_BARK_REWARD_ENABLED
    // "He went quiet after barking" arrives as a counter in a response header (the body format is unchanged).
    // The first value after boot is only a baseline, a value that is not newer is ignored, and an event older
    // than BARK_MAX_AGE_S is never rewarded (a delayed poll must not fire a stale treat).
    if (http.hasHeader("X-CANIS-Quiet-Seq")) {
      unsigned long seq = strtoul(http.header("X-CANIS-Quiet-Seq").c_str(), nullptr, 10);
      float age = http.hasHeader("X-CANIS-Quiet-Age") ? http.header("X-CANIS-Quiet-Age").toFloat() : -1.0f;
      if (!quietSeqSynced) {
        quietSeq = seq; quietSeqSynced = true;
      } else if (seq > quietSeq) {
        quietSeq = seq;
        if (age >= 0.0f && age <= (float)CANIS_BARK_MAX_AGE_S) reward("quiet after barking");
        else Serial.printf("  [BARK] quiet event ignored (age %.1f s)\n", age);
      }
    }
#endif
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
