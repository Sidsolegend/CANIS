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

  NOTE: no arduino_secrets.h value is reproduced here; the include and the
  SECRET_* names are the same public interface as the original.

  Serial Monitor @ 115200.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <ctype.h>             // tolower() for zone-label matching
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

// ---------- Movement sub-behavior ----------
char          moveCmd[12]    = "";
unsigned long moveUntil      = 0;
unsigned long lastMovePulse  = 0;
int           motionPhase    = 0;

int EXPOSURE_LEVEL = CANIS_EXPOSURE_DEFAULT;

// ---------- WiFi reconnect watchdog [OPT-5] ----------
unsigned long lastWifiCheck = 0;

// ---------- Dispense ACK bookkeeping [OPT-7] ----------
#if CANIS_USE_DISPENSE_ACK
bool          awaitingAck   = false;
unsigned long ackDeadline   = 0;
char          ackBuf[8]     = "";
int           ackLen        = 0;
#endif

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
  Serial.println("Keys:  t=trigger  r=reset  d=dispense  m=cycle exposure  f=test fwd  s=stop");
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
  pollCommand(now);
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
      Serial2.println("DISPENSE");
      Serial.println("  [MANUAL] sent DISPENSE to Arduino");
    } else if (c == 'm') {
      EXPOSURE_LEVEL = (EXPOSURE_LEVEL + 1) % 3;
      Serial.print("  [MANUAL] exposure level -> "); Serial.println(EXPOSURE_LEVEL);
    } else if (c == 'f') {
      startMotion("FORWARD", 1500, millis());
      motionPhase = 0;
      Serial.println("  [MANUAL] test FORWARD (1.5s)");
    } else if (c == 's') {
      stopMotion(millis());
      Serial.println("  [MANUAL] STOP");
    }
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
  ackDeadline  = now + CANIS_ACK_TIMEOUT_MS;
  ackLen       = 0;
  Serial2.println("DISPENSE");
  Serial.print("  [OUTPUT] DISPENSE sent, awaiting ACK -- "); Serial.println(why);
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
      if (awaitingAck && strcmp(ackBuf, "ACK") == 0) {
        rewardCount++;
        awaitingAck = false;
        lastReport  = 0;   // reflect the delivered treat immediately
        Serial.print("  [OUTPUT] >>> REWARD confirmed (ACK). count="); Serial.println(rewardCount);
      }
      ackLen = 0;
    } else if (ackLen < (int)sizeof(ackBuf) - 1) {
      ackBuf[ackLen++] = c;
    }
  }
  if (awaitingAck && (long)(now - ackDeadline) >= 0) {
    awaitingAck = false;
    Serial.println("  [OUTPUT] !! dispense ACK timeout (drop not confirmed) — not counted");
  }
}
#endif

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
  if ((long)(now - postBackoffUntil) < 0) return;
  if (now - lastReport < CANIS_REPORT_MS) return;
  lastReport = now;
  if (WiFi.status() != WL_CONNECTED) return;

  char body[256];
  snprintf(body, sizeof(body),
    "{\"state\":\"%s\",\"arousal\":%.1f,\"arousal_max\":%.1f,"
    "\"intervention_count\":%d,\"reward_count\":%d,\"uptime_sec\":%lu}",
    name(state), arousal, CANIS_AROUSAL_MAX,
    interventionCount, rewardCount, (unsigned long)(now / 1000));

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

// ---------- Poll the Pi for vision commands ----------
void pollCommand(unsigned long now) {
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
