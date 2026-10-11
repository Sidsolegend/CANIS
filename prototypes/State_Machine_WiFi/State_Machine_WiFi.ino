/*
  CANIS - State Machine + WiFi reporting (ESP32)
  ----------------------------------------------
  Same brain as State_Machine_CANIS.ino, plus it pushes its state to the Pi's
  Flask dashboard over WiFi (HTTP POST /ingest) once a second and on every
  state change.

  This is the ESP32<->Pi link. The ESP32 here is still driven by keyboard
  triggers over the USB Serial Monitor ('t' / 'r') - exactly like the offline
  version - so you can prove the whole pipe works before any sensor is wired.

  SETUP before flashing:
    1. Put your WiFi name/password in WIFI_SSID / WIFI_PASS below.
    2. Put the Pi's IP in PI_HOST. Find it on the Pi with:  hostname -I
       (use the same WiFi network for the Pi, the ESP32, and your phone).
    3. Start the dashboard on the Pi:  python3 app.py
    4. Flash this, open Serial Monitor @ 115200, tap 't' a few times, and watch
       the dashboard switch from "simulator" to "live data - ESP32".

  Boards Manager: install "esp32 by Espressif". WiFi.h + HTTPClient.h ship with it.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include "arduino_secrets.h"   // WiFi/Pi creds - gitignored, see arduino_secrets.h.example

const char* WIFI_SSID = SECRET_SSID;
const char* WIFI_PASS = SECRET_PASS;
const char* PI_HOST   = SECRET_PI_HOST;
const int   PI_PORT   = 5000;

// ---------- Tunables (mirror State_Machine_CANIS.ino) ----------
const float AROUSAL_MAX      = 100.0;
const float DECAY_PER_SEC    = 12.0;
const float TRIGGER_BUMP     = 25.0;
const float AROUSAL_ENTER    = 20.0;
const float CORRECTION_ENTER = 70.0;
const unsigned long CALM_REWARD_MS = 15000;

const unsigned long REPORT_MS = 1000;   // push to Pi at 1 Hz (plus on change)

// ---------- State ----------
enum State { CALM, AROUSAL, CORRECTION };
State state = CALM, prevState = CALM;

float arousal = 0.0;
unsigned long lastTick = 0, calmSince = 0, lastCalmReward = 0;
unsigned long lastCorrectionAction = 0, lastLog = 0, lastReport = 0;

int rewardCount = 0;
int interventionCount = 0;

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nCANIS state machine (WiFi) online.");
  Serial.println("Keys:  t = trigger (bad behavior)   r = reset to calm");
  connectWiFi();
  lastTick = millis();
  calmSince = millis();
}

void loop() {
  unsigned long now = millis();
  float dt = (now - lastTick) / 1000.0;
  lastTick = now;

  handleInput();
  decayArousal(dt);
  updateState();          // may flip a flag that forces an immediate report
  runStateBehavior(now);
  logStatus(now);
  reportToPi(now);

  delay(20);
}

// ---------- WiFi ----------
void connectWiFi() {
  Serial.print("WiFi: connecting to "); Serial.println(WIFI_SSID);
  // Hardening (see WiFi_Connect_Test): erase stale creds + kill modem sleep.
  // Without this, Nest Wifi reliably timed out on first connect.
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
    Serial.println("\nWiFi FAILED - running offline (Serial only). Check SSID/PASS.");
  }
}

// ---------- Inputs (placeholder for real sensors - Phase 4) ----------
void handleInput() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 't') {
      arousal += TRIGGER_BUMP;
      if (arousal > AROUSAL_MAX) arousal = AROUSAL_MAX;
      Serial.print("  [SENSOR] trigger -> arousal "); Serial.println(arousal, 1);
    } else if (c == 'r') {
      arousal = 0;
      Serial.println("  [SENSOR] reset -> arousal 0");
    }
  }
}

void decayArousal(float dt) {
  arousal -= DECAY_PER_SEC * dt;
  if (arousal < 0) arousal = 0;
}

void updateState() {
  prevState = state;

  if      (arousal >= CORRECTION_ENTER) state = CORRECTION;
  else if (arousal >= AROUSAL_ENTER)    state = AROUSAL;
  else                                  state = CALM;

  if (state != prevState) {
    Serial.print(">>> STATE: ");
    Serial.print(name(prevState)); Serial.print(" -> "); Serial.println(name(state));

    if (state == CORRECTION) interventionCount++;

    // Reward LEAVING - dropping back to CALM means the dog disengaged.
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
        Serial.println("  [OUTPUT] would arm marker buzzer + orient toward zone");
      }
      break;
  }
}

void reward(const char* why) {
  rewardCount++;
  Serial.print("  [OUTPUT] >>> REWARD (marker buzzer + dispense treat) -- ");
  Serial.println(why);
  // LATER: send DISPENSE to the Arduino over serial + fire the marker buzzer.
}

void logStatus(unsigned long now) {
  if (now - lastLog >= 1000) {
    lastLog = now;
    Serial.print("["); Serial.print(name(state));
    Serial.print("] arousal="); Serial.println(arousal, 1);
  }
}

// ---------- Push state to the Pi ----------
void reportToPi(unsigned long now) {
  if (now - lastReport < REPORT_MS) return;
  lastReport = now;
  if (WiFi.status() != WL_CONNECTED) return;

  // No wall clock on the ESP32 - send raw counters; the Pi stamps reward times.
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
  http.setTimeout(800);                 // don't stall the loop if the Pi is down
  int code = http.POST((uint8_t*)body, strlen(body));
  if (code <= 0) Serial.printf("  [NET] POST failed (%s)\n", http.errorToString(code).c_str());
  http.end();
}

const char* name(State s) {
  switch (s) { case CALM: return "CALM"; case AROUSAL: return "AROUSAL"; case CORRECTION: return "CORRECTION"; }
  return "?";
}
