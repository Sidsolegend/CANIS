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
#include "arduino_secrets.h"   // WiFi/Pi creds — gitignored, see arduino_secrets.h.example

// ---------- WiFi / Pi ----------
const char* WIFI_SSID = SECRET_SSID;
const char* WIFI_PASS = SECRET_PASS;
const char* PI_HOST   = SECRET_PI_HOST;
const int   PI_PORT   = 5000;
const unsigned long REPORT_MS = 1000;   // push to Pi at 1 Hz (plus on change)

// ---------- Sensor pins (from Sensors_Test) ----------
const int PIR_PIN  = 27;   // PIR OUT (3.3V logic, read directly)
const int TRIG_PIN = 26;   // ultrasonic trigger
const int ECHO_PIN = 25;   // ultrasonic echo (THROUGH the 1k series resistor)

// ---------- Arduino dispenser link (ESP32 -> Arduino, one-way) ----------
// Serial2 remapped to accessible-side pins (GPIO 16/17 are on the buried side).
// Only TX is wired: ESP32 GPIO 13 -> Arduino pin 10 (SoftwareSerial RX), + GND.
const int ARD_TX_PIN = 13;   // ESP32 TX2 -> Arduino pin 10
const int ARD_RX_PIN = 14;   // unused (we only send); leave unwired

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
const unsigned long CALM_REWARD_MS = 15000;

// NEW seam tunable: how fast a confirmed detection drives arousal UP. Must beat
// DECAY_PER_SEC so presence climbs (here net +18/s while detected). Empirical —
// tune against real Shifu behavior alongside the thresholds above.
const float AROUSAL_RISE_PER_SEC = 30.0;

// ---------- Output tunables ----------
const int CORRECTION_TONE_FREQ = 800;   // disengage cue (distinct from 2000Hz reward marker)
const int CORRECTION_TONE_MS   = 150;

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

Confirmed pir;        // confirmed motion
Confirmed zone;       // confirmed "something is in the zone"
bool detectionActive = false;   // pir.state || zone.state -> drives arousal

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

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PIR_PIN, INPUT_PULLDOWN);   // reads steady LOW if PIR is unplugged (no phantom motion)
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);
  pinMode(BUZZER_PIN, OUTPUT);

  Serial2.begin(9600, SERIAL_8N1, ARD_RX_PIN, ARD_TX_PIN);  // link to Arduino dispenser

  Serial.println("\nCANIS core online. PIR warming up (~60s).");
  Serial.println("Keys:  t = manual trigger   r = reset to calm   d = test dispense");
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
  readSensors(now);       // real triggers -> sets detectionActive
  updateArousal(dt);      // detection drives arousal up; decay always pulls down
  updateState();          // recompute state + transitions (verbatim)
  runStateBehavior(now);  // act on current state (verbatim)
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
    }
  }
}

// ---------- Real sensors (verbatim logic from Sensors_Test) ----------
void readSensors(unsigned long now) {
  if (now - lastSample < SAMPLE_MS) return;
  lastSample = now;

  bool warm = (now - bootTime) >= WARMUP_MS;

  bool pirRaw = digitalRead(PIR_PIN) == HIGH;
  if (!warm) pirRaw = false;                  // mask warm-up garbage
  if (pir.update(pirRaw))
    Serial.printf("  [SENSOR] PIR  -> %s\n",  pir.state  ? "MOTION"  : "clear");

  float dist = readDistanceCm();
  bool inZoneRaw = (dist > 0 && dist <= ZONE_CM);
  if (zone.update(inZoneRaw))
    Serial.printf("  [SENSOR] ZONE -> %s\n", zone.state ? "IN ZONE" : "clear");

  detectionActive = pir.state || zone.state;  // this is the old 't', made real
}

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long dur = pulseIn(ECHO_PIN, HIGH, 25000);
  if (dur == 0) return -1.0;
  return dur * 0.0343 / 2.0;
}

// ---------- Arousal: the SEAM ----------
// Sustained detection climbs; decay always bleeds off. Net +18/s while detected.
void updateArousal(float dt) {
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
      if (now - calmSince >= CALM_REWARD_MS && now - lastCalmReward >= CALM_REWARD_MS) {
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
        Serial.println("  [OUTPUT] correction cue (buzzer) + would orient toward zone");
      }
      break;
  }
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
  if (now < postBackoffUntil) return;          // backing off a dead Pi -> stay responsive
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

const char* name(State s) {
  switch (s) { case CALM: return "CALM"; case AROUSAL: return "AROUSAL"; case CORRECTION: return "CORRECTION"; }
  return "?";
}
