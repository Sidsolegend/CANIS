/*
  CANIS — Core (ESP32)
  --------------------
  The closed loop, merged from two tested sketches:
    - Sensors_Test.ino       (PIR + ultrasonic with confirmation filtering)
    - State_Machine_WiFi.ino (arousal state machine + push to the Pi dashboard)

  world -> sensors -> confirmation -> arousal -> state machine -> dashboard

  WHAT CHANGED vs the two originals: nothing in the logic itself. The only new
  piece is the SEAM between them. In State_Machine_WiFi the 't' key was a
  one-shot arousal bump. A real sensor instead sees SUSTAINED presence, so a
  confirmed detection must HOLD arousal up while it lasts — otherwise a dog that
  lingers in the zone would decay back to CALM and get "rewarded for leaving"
  while still sitting there. So:
      while a detection is confirmed-active -> arousal RISES (beats decay)
      when it clears                        -> decay wins -> settles -> reward
  The 't'/'r' keys are kept as a manual override for bench testing.

  Everything else (state thresholds, decay, reward-on-leaving, sensor
  confirmation, WiFi push) is verbatim from the tested sketches.

  Serial Monitor @ 115200.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <ctype.h>             // tolower() for zone-label matching
#include "arduino_secrets.h"   // WiFi/Pi creds — gitignored, see arduino_secrets.h.example

// ---------- WiFi / Pi ----------
const char* WIFI_SSID = SECRET_SSID;
const char* WIFI_PASS = SECRET_PASS;
const char* PI_HOST   = SECRET_PI_HOST;
const int   PI_PORT   = 5000;
const unsigned long REPORT_MS = 1000;   // push state to Pi at 1 Hz (plus on change)
const unsigned long POLL_MS   = 1000;   // poll Pi for vision commands at 1 Hz

// ---------- Sensor pins ----------
const int PIR_PIN  = 27;   // PIR OUT (3.3V logic, read directly)

// THREE ultrasonics, mounted left / centre / right so the trio also gives a
// local bearing on the dog. Fired SEQUENTIALLY (a shared/parallel trigger makes
// their echoes cross-talk). Each ECHO comes in THROUGH its own 1k series
// resistor (5V->3.3V protection). 34/35 are input-only ESP32 pins — fine for
// ECHO. See hardware/WIRING.md for the full table.
const int   US_TRIG[3] = { 32, 26, 14 };          // LEFT, CENTRE, RIGHT
const int   US_ECHO[3] = { 34, 25, 35 };          // LEFT, CENTRE, RIGHT
const char* US_DIR[3]  = { "LEFT", "FORWARD", "RIGHT" };  // orient hint per sensor
// CENTRE (26/25) is the original sensor. The waterproof SR04M-2 has a ~25cm
// blind zone, so mount it on the longest-range position (whichever that is) and
// keep ZONE_CM just above its blind spot. Set NUM_US to 2 to disable the third.
const int   NUM_US = 3;
const unsigned long ECHO_TIMEOUT_US = 12000;      // ~2m cap; close-range zone needs no more

// ---------- Arduino dispenser link (ESP32 -> Arduino, one-way) ----------
// Serial2 remapped to accessible-side pins (GPIO 16/17 are on the buried side).
// Only TX is wired: ESP32 GPIO 13 -> Arduino pin 10 (SoftwareSerial RX), + GND.
// RX is set to -1 (unused) so the UART doesn't claim GPIO 14 — that pin now
// drives the RIGHT ultrasonic's trigger.
const int ARD_TX_PIN = 13;   // ESP32 TX2 -> Arduino pin 10
const int ARD_RX_PIN = -1;   // unused: we only send (frees GPIO 14 for US_TRIG[2])

// ---------- Buzzer (the brain's own marker/cue output) ----------
// Buzzer + -> GPIO 33, buzzer - -> GND. Distinct from the Arduino's reward
// marker so the "disengage" cue and the "treat earned" marker never blur.
const int BUZZER_PIN = 33;

// ---------- Sensor tunables (from Sensors_Test) ----------
const int   CONFIRM_N   = 3;            // consecutive agreeing reads to flip confirmed
const int   SAMPLE_MS   = 100;          // 10 Hz sampling -> 3 reads = 300ms to confirm
const float ZONE_CM     = 30.0;         // "in the forbidden zone" if closer than this
const unsigned long WARMUP_MS = 60000;  // ignore PIR for the first 60s

// ---------- Arousal / state tunables (from State_Machine_WiFi) ----------
const float AROUSAL_MAX      = 100.0;
const float DECAY_PER_SEC    = 12.0;    // bleed-off when nothing is detected
const float TRIGGER_BUMP     = 25.0;    // manual 't' key bump (bench testing only)
const float AROUSAL_ENTER    = 20.0;    // CALM -> AROUSAL above this
const float CORRECTION_ENTER = 70.0;    // AROUSAL -> CORRECTION above this
const unsigned long CALM_REWARD_MS = 30000;  // sustained-calm reward interval; keep >= REWARD_COOLDOWN_MS

// NEW seam tunable: how fast a confirmed detection drives arousal UP. Must beat
// DECAY_PER_SEC so presence climbs (here net +18/s while detected). Empirical —
// tune against real Shifu behavior alongside the thresholds above.
const float AROUSAL_RISE_PER_SEC = 30.0;

// ---------- Output tunables ----------
const int CORRECTION_TONE_FREQ = 800;   // disengage cue (distinct from 2000Hz reward marker)
const int CORRECTION_TONE_MS   = 150;

// ---------- Movement / exposure (THE welfare knob) ----------
// Shifu is afraid of moving objects, so CANIS introduces motion GRADUALLY over
// weeks/months. EXPOSURE_LEVEL is the master gate on all physical movement and
// the single most important safety/welfare setting in the project:
//   0 = no movement at all (buzzer-only correction)   <-- SAFE DEFAULT
//   1 = orient only: pivot to face the zone, never approach
//   2 = orient + a brief, gentle forward approach
// Raise by ONE level only after his logged fear response shows he's comfortable
// at the current one. Never skip levels. (Toggle live with the 'm' key.)
int EXPOSURE_LEVEL = 0;

// Re-send cadence to feed the Arduino's motion watchdog. MUST stay below the
// Arduino's MOTION_TIMEOUT_MS (1000 ms) or the robot stutters; if the link
// drops, the Arduino auto-stops within ~1 s. (See Treat_Dispenser_CANIS.ino.)
const unsigned long MOVE_PULSE_MS = 500;
const unsigned long ORIENT_MS     = 700;   // pivot-toward-zone duration per correction cycle
const unsigned long APPROACH_MS   = 400;   // brief forward nudge after orienting (level 2 only)

// Reward safety: stop the hopper from emptying. Gates BOTH reward paths.
const unsigned long REWARD_COOLDOWN_MS = 20000;  // min gap between any two treats
const int MAX_SESSION_REWARDS          = 30;     // hard cap until reboot

// ---------- Confirmation filter (from Sensors_Test) ----------
struct Confirmed {
  bool state = false;
  int  posStreak = 0;
  int  negStreak = 0;
  bool update(bool raw) {
    if (raw) { posStreak++; negStreak = 0; }
    else     { negStreak++; posStreak = 0; }
    bool before = state;
    if (!state && posStreak >= CONFIRM_N) state = true;
    if ( state && negStreak >= CONFIRM_N) state = false;
    return state != before;
  }
};

Confirmed pir;            // confirmed motion
Confirmed usZone[3];      // per-sensor confirmed "something in the zone" (L/C/R)
float     usCm[3] = { -1, -1, -1 };   // last good distance per sensor (for nearest pick)
bool      zoneActive = false;         // any ultrasonic confirmed in-zone
bool detectionActive = false;   // pir.state || zoneActive || visionActive -> drives arousal

// ---------- State (from State_Machine_WiFi) ----------
enum State { CALM, AROUSAL, CORRECTION };
State state = CALM, prevState = CALM;

float arousal = 0.0;
unsigned long lastTick = 0, calmSince = 0, lastCalmReward = 0;
unsigned long lastCorrectionAction = 0, lastLog = 0, lastReport = 0;
unsigned long bootTime = 0, lastSample = 0;

int rewardCount = 0;
int interventionCount = 0;
unsigned long lastRewardMs = 0;   // for the reward cooldown

unsigned long lastPoll = 0;       // vision-command polling
bool visionActive = false;        // a fresh vision detection from the Pi
char visionZone[24] = "";         // which zone the camera reported (fixed buffer, no heap churn)

// ---------- Movement sub-behavior ----------
char          moveCmd[12]    = ""; // motion we're currently pulsing ("" = none)
unsigned long moveUntil      = 0;  // stop this motion at/after this time
unsigned long lastMovePulse  = 0;  // last time we re-sent moveCmd (watchdog feed)
int           motionPhase    = 0;  // 0 none, 1 orienting, 2 approaching

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PIR_PIN, INPUT_PULLDOWN);   // reads steady LOW if PIR is unplugged (no phantom motion)
  for (int i = 0; i < NUM_US; i++) {
    pinMode(US_TRIG[i], OUTPUT);
    pinMode(US_ECHO[i], INPUT);
    digitalWrite(US_TRIG[i], LOW);
  }
  pinMode(BUZZER_PIN, OUTPUT);

  Serial2.begin(9600, SERIAL_8N1, ARD_RX_PIN, ARD_TX_PIN);  // link to Arduino dispenser

  Serial.println("\nCANIS core online. PIR warming up (~60s).");
  Serial.println("Keys:  t=trigger  r=reset  d=dispense  m=cycle exposure  f=test fwd  s=stop");
  Serial.print  ("Exposure level: "); Serial.println(EXPOSURE_LEVEL);
  connectWiFi();

  bootTime = millis();
  lastTick = millis();
  calmSince = millis();
}

void loop() {
  unsigned long now = millis();
  float dt = (now - lastTick) / 1000.0;
  lastTick = now;

  handleInput();          // manual t/r override
  readSensors(now);       // local PIR + ultrasonic
  pollCommand(now);       // vision detections from the Pi (remote trigger)
  updateArousal(dt);      // any detection drives arousal up; decay always pulls down
  updateState();          // recompute state + transitions
  runStateBehavior(now);  // act on current state
  serviceMotion(now);     // keep any active motion alive + end it on time
  logStatus(now);
  reportToPi(now);

  delay(20);
}

// ---------- WiFi (verbatim from State_Machine_WiFi, with the Nest hardening) ----------
void connectWiFi() {
  Serial.print("WiFi: connecting to "); Serial.println(WIFI_SSID);
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);     // erase any old config
  delay(200);
  WiFi.setSleep(false);            // no modem sleep
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

// ---------- Manual override (kept from State_Machine_WiFi) ----------
void handleInput() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 't') {
      arousal += TRIGGER_BUMP;
      if (arousal > AROUSAL_MAX) arousal = AROUSAL_MAX;
      Serial.print("  [MANUAL] trigger -> arousal "); Serial.println(arousal, 1);
    } else if (c == 'r') {
      arousal = 0;
      Serial.println("  [MANUAL] reset -> arousal 0");
    } else if (c == 'd') {
      Serial2.println("DISPENSE");   // test the Arduino link in isolation
      Serial.println("  [MANUAL] sent DISPENSE to Arduino");
    } else if (c == 'm') {
      EXPOSURE_LEVEL = (EXPOSURE_LEVEL + 1) % 3;   // 0 -> 1 -> 2 -> 0
      Serial.print("  [MANUAL] exposure level -> "); Serial.println(EXPOSURE_LEVEL);
    } else if (c == 'f') {
      startMotion("FORWARD", 1500, millis());      // bench-test the motor link
      motionPhase = 0;
      Serial.println("  [MANUAL] test FORWARD (1.5s)");
    } else if (c == 's') {
      stopMotion(millis());
      Serial.println("  [MANUAL] STOP");
    }
  }
}

// ---------- Real sensors (PIR + 3 ultrasonics) ----------
void readSensors(unsigned long now) {
  if (now - lastSample < SAMPLE_MS) return;
  lastSample = now;

  bool warm = (now - bootTime) >= WARMUP_MS;

  bool pirRaw = digitalRead(PIR_PIN) == HIGH;
  if (!warm) pirRaw = false;                  // mask warm-up garbage
  if (pir.update(pirRaw))
    Serial.printf("  [SENSOR] PIR  -> %s\n",  pir.state  ? "MOTION"  : "clear");

  // Fire the ultrasonics one at a time; a short gap lets each burst's echoes
  // die before the next sensor pings, so they don't hear each other.
  bool anyZone = false;
  for (int i = 0; i < NUM_US; i++) {
    float dist = readDistanceCm(US_TRIG[i], US_ECHO[i]);
    usCm[i] = dist;
    bool inZoneRaw = (dist > 0 && dist <= ZONE_CM);
    if (usZone[i].update(inZoneRaw))
      Serial.printf("  [SENSOR] ZONE %-7s -> %s\n",
                    US_DIR[i], usZone[i].state ? "IN ZONE" : "clear");
    if (usZone[i].state) anyZone = true;
    delay(5);   // inter-sensor settle (anti-cross-talk)
  }
  zoneActive = anyZone;
}

float readDistanceCm(int trig, int echo) {
  digitalWrite(trig, LOW);  delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10);
  digitalWrite(trig, LOW);
  unsigned long dur = pulseIn(echo, HIGH, ECHO_TIMEOUT_US);
  if (dur == 0) return -1.0;
  return dur * 0.0343 / 2.0;
}

// Nearest confirmed-in-zone ultrasonic -> a local heading toward the dog.
// Returns nullptr if no sensor is currently confirmed in-zone.
const char* localBearing() {
  int best = -1; float bestCm = 1e9;
  for (int i = 0; i < NUM_US; i++) {
    if (usZone[i].state && usCm[i] > 0 && usCm[i] < bestCm) { bestCm = usCm[i]; best = i; }
  }
  return best >= 0 ? US_DIR[best] : nullptr;
}

// ---------- Arousal: the SEAM ----------
// Sustained detection climbs; decay always bleeds off. Net +18/s while detected.
void updateArousal(float dt) {
  // Any detection drives arousal: local sensors OR a remote vision call from the Pi.
  detectionActive = pir.state || zoneActive || visionActive;
  if (detectionActive) arousal += AROUSAL_RISE_PER_SEC * dt;
  arousal -= DECAY_PER_SEC * dt;
  if (arousal > AROUSAL_MAX) arousal = AROUSAL_MAX;
  if (arousal < 0)           arousal = 0;
}

// ---------- State machine (verbatim from State_Machine_WiFi) ----------
void updateState() {
  prevState = state;

  if      (arousal >= CORRECTION_ENTER) state = CORRECTION;
  else if (arousal >= AROUSAL_ENTER)    state = AROUSAL;
  else                                  state = CALM;

  if (state != prevState) {
    Serial.print(">>> STATE: ");
    Serial.print(name(prevState)); Serial.print(" -> "); Serial.println(name(state));

    if (state == CORRECTION) interventionCount++;

    // Leaving CORRECTION: halt any motion immediately (don't wait for the
    // watchdog) — the dog disengaged, so CANIS must stop moving at once.
    if (prevState == CORRECTION && state != CORRECTION) stopMotion(millis());

    // Reward LEAVING — dropping back to CALM means the dog disengaged.
    if (state == CALM && (prevState == AROUSAL || prevState == CORRECTION)) {
      reward("settled / left zone");
      lastCalmReward = millis();
    }
    if (state == CALM) calmSince = millis();

    lastReport = 0;   // force an immediate push so the dashboard reacts instantly
  }
}

void runStateBehavior(unsigned long now) {
  switch (state) {
    case CALM:
      // Don't reward calm until warm-up is over: during warm-up the PIR is
      // masked, so we haven't truly been watching yet. Avoids a wasted treat
      // ~15s after every boot. (The leaving-reward still works during warm-up,
      // since the ultrasonic is trusted from the start.)
      if (now - bootTime >= WARMUP_MS &&
          now - calmSince >= CALM_REWARD_MS && now - lastCalmReward >= CALM_REWARD_MS) {
        reward("sustained calm");
        lastCalmReward = now;
      }
      break;
    case AROUSAL:
      break;
    case CORRECTION:
      if (now - lastCorrectionAction >= 2000) {
        lastCorrectionAction = now;
        tone(BUZZER_PIN, CORRECTION_TONE_FREQ, CORRECTION_TONE_MS);  // disengage cue
        Serial.print("  [OUTPUT] correction cue (buzzer)");
        if (EXPOSURE_LEVEL >= 1) {
          const char* dir = localBearing();           // our own ultrasonics first...
          if (!dir) dir = zoneToDir(visionZone);      // ...else fall back to the Pi's vision label
          startMotion(dir, ORIENT_MS, now);           // pivot to face the zone
          motionPhase = 1;                            // serviceMotion may chain an approach (level 2)
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

// ---------- Movement (gated by EXPOSURE_LEVEL; fed to the Arduino watchdog) ----------
// Start a bounded motion. We fire the command now and serviceMotion() re-pulses
// it every MOVE_PULSE_MS until moveUntil — so the Arduino's watchdog keeps the
// motors alive while we mean it, but halts them within ~1s if the link drops.
void startMotion(const char* cmd, unsigned long durMs, unsigned long now) {
  strncpy(moveCmd, cmd, sizeof(moveCmd) - 1);
  moveCmd[sizeof(moveCmd) - 1] = '\0';
  moveUntil     = now + durMs;
  lastMovePulse = now;
  Serial2.println(moveCmd);          // fire immediately
}

void stopMotion(unsigned long now) {
  (void)now;
  if (moveCmd[0]) Serial2.println("STOP");
  moveCmd[0]  = '\0';
  moveUntil   = 0;
  motionPhase = 0;
}

// Keep the active motion alive and end it on time. On finishing an orient at
// exposure level 2, chain a brief forward approach (if still correcting).
void serviceMotion(unsigned long now) {
  if (!moveCmd[0]) return;

  if ((long)(now - moveUntil) >= 0) {              // this motion's time is up
    if (motionPhase == 1 && EXPOSURE_LEVEL >= 2 && state == CORRECTION) {
      startMotion("FORWARD", APPROACH_MS, now);    // orient -> gentle approach
      motionPhase = 2;
    } else {
      stopMotion(now);
    }
    return;
  }

  if (now - lastMovePulse >= MOVE_PULSE_MS) {       // feed the watchdog
    Serial2.println(moveCmd);
    lastMovePulse = now;
  }
}

// Map the Pi's vision zone label to a turn. Zone labels are room-specific, so
// EDIT THIS to match your layout / camera framing. Until then: a label
// containing "left"/"right" pivots that way; anything else drives straight.
// (A true bearing needs a zone->angle map or an onboard heading sensor — Phase 8+.)
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

void reward(const char* why) {
  unsigned long now = millis();

  // Cooldown: never two treats within REWARD_COOLDOWN_MS (stops rapid re-treating
  // from in/out bouncing or repeated sustained-calm rewards).
  if (lastRewardMs != 0 && now - lastRewardMs < REWARD_COOLDOWN_MS) {
    Serial.print("  [OUTPUT] reward held (cooldown) -- "); Serial.println(why);
    return;
  }
  // Session cap: don't empty the hopper. Resets on reboot.
  if (rewardCount >= MAX_SESSION_REWARDS) {
    Serial.println("  [OUTPUT] reward held (session cap reached)");
    return;
  }

  lastRewardMs = now;
  rewardCount++;
  Serial2.println("DISPENSE");   // real treat: Arduino fires marker buzzer + gate
  Serial.print("  [OUTPUT] >>> REWARD (sent DISPENSE) -- ");
  Serial.println(why);
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

// ---------- Push state to the Pi (verbatim from State_Machine_WiFi) ----------
unsigned long postBackoffUntil = 0;   // if the Pi is unreachable, stop hammering it

void reportToPi(unsigned long now) {
  if ((long)(now - postBackoffUntil) < 0) return;   // rollover-safe: still backing off a dead Pi
  if (now - lastReport < REPORT_MS) return;
  lastReport = now;
  if (WiFi.status() != WL_CONNECTED) return;

  char body[256];
  snprintf(body, sizeof(body),
    "{\"state\":\"%s\",\"arousal\":%.1f,\"arousal_max\":%.1f,"
    "\"intervention_count\":%d,\"reward_count\":%d,\"uptime_sec\":%lu}",
    name(state), arousal, AROUSAL_MAX,
    interventionCount, rewardCount, (unsigned long)(now / 1000));

  char url[64];
  snprintf(url, sizeof(url), "http://%s:%d/ingest", PI_HOST, PI_PORT);

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(300);                          // short: don't stall the control loop
  int code = http.POST((uint8_t*)body, strlen(body));
  if (code <= 0) {
    Serial.printf("  [NET] POST failed (%s) — backing off 5s\n", http.errorToString(code).c_str());
    postBackoffUntil = now + 5000;               // Pi unreachable: pause POSTs, keep loop snappy
  }
  http.end();
}

// ---------- Poll the Pi for vision commands (Pi -> ESP32) ----------
// GET /command returns the detected zone name, or "NONE". A non-NONE reply is a
// remote detection that drives arousal just like the local sensors. Shares the
// reportToPi back-off so an unreachable Pi never stalls the loop.
void pollCommand(unsigned long now) {
  if ((long)(now - postBackoffUntil) < 0) return;   // rollover-safe: shared dead-Pi backoff
  if (now - lastPoll < POLL_MS) return;
  lastPoll = now;
  if (WiFi.status() != WL_CONNECTED) return;

  char url[64];
  snprintf(url, sizeof(url), "http://%s:%d/command", PI_HOST, PI_PORT);

  HTTPClient http;
  http.begin(url);
  http.setTimeout(300);
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();   // transient, freed at end of call
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
      lastReport = 0;   // push to dashboard immediately on a vision change
    }
  } else if (code <= 0) {
    postBackoffUntil = now + 5000;   // Pi gone: back off poll + report together
  }
  http.end();
}

const char* name(State s) {
  switch (s) { case CALM: return "CALM"; case AROUSAL: return "AROUSAL"; case CORRECTION: return "CORRECTION"; }
  return "?";
}
