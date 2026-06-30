/*
  CANIS — Sensor test (ESP32)   [Phase 4]
  ---------------------------------------
  Standalone. Proves the PIR + THREE ultrasonics give CLEAN, CONFIRMED readings
  before they get wired into the state machine. No WiFi, no dashboard — just
  sensors. Pins match CANIS_Core.ino, so a clean run here means the full brain
  will read the same wiring correctly.

  The point of this phase isn't the wiring, it's the CONFIRMATION LOGIC: a single
  positive blip (a curtain, a fan, a shadow, a stray echo) must NOT count. We
  require N consecutive agreeing reads before flipping "confirmed" — hysteresis
  that kills false triggers. Watch the RAW vs CONFIRMED columns to see it work.

  WIRING (ESP32 dev board) — see hardware/WIRING.md for the table:
    PIR  VCC -> 5V (VIN)   PIR GND -> GND   PIR OUT -> GPIO 27 (3.3V safe)
    All ultrasonic VCC -> 5V (VIN), GND -> GND.
    Each ECHO is 5V: bring it in THROUGH a 1k series resistor to the ESP32 pin.

      sensor   TRIG    ECHO (via 1k)
      LEFT      32       34          (34/35 are input-only ESP32 pins — fine for ECHO)
      CENTRE    26       25
      RIGHT     14       35

  The three are fired SEQUENTIALLY with a short gap so their echoes don't
  cross-talk. The waterproof SR04M-2 has a ~25cm blind zone — mount it on the
  longest-range spot and don't expect valid reads closer than that.

  PIR module: set the jumper to "H" (repeat trigger). Give it ~60s to warm up
  after power-on; early reads are garbage and the code masks them.

  Serial Monitor @ 115200.
*/

// ---------- Pins ----------
const int PIR_PIN = 27;   // PIR OUT (3.3V logic, read directly)

const int   US_TRIG[3] = { 32, 26, 14 };          // LEFT, CENTRE, RIGHT
const int   US_ECHO[3] = { 34, 25, 35 };          // LEFT, CENTRE, RIGHT
const char* US_NAME[3] = { "LEFT", "CENTRE", "RIGHT" };
const int   NUM_US     = 3;

// ---------- Tunables ----------
const int   CONFIRM_N   = 3;      // consecutive agreeing reads to flip confirmed
const int   SAMPLE_MS   = 100;    // 10 Hz sampling -> 3 reads = 300ms to confirm
const float ZONE_CM     = 30.0;   // "in the forbidden zone" if closer than this
const unsigned long WARMUP_MS = 60000;  // ignore PIR for the first 60s
const unsigned long ECHO_TIMEOUT_US = 12000;  // ~2m cap

// ---------- Debounced/confirmed state ----------
struct Confirmed {
  bool state = false;       // the trusted, hysteresis-filtered value
  int  posStreak = 0;       // consecutive raw-true reads
  int  negStreak = 0;       // consecutive raw-false reads

  // feed a raw reading; returns true if the confirmed state just changed
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
Confirmed usZone[3];      // confirmed "something in the zone" per sensor (L/C/R)

unsigned long bootTime = 0, lastSample = 0;

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PIR_PIN, INPUT);
  for (int i = 0; i < NUM_US; i++) {
    pinMode(US_TRIG[i], OUTPUT);
    pinMode(US_ECHO[i], INPUT);
    digitalWrite(US_TRIG[i], LOW);
  }
  bootTime = millis();
  Serial.println("\nCANIS sensor test (PIR + 3 ultrasonics). PIR warming up (~60s)...");
  Serial.println("cols:  PIRok | L(cm) C(cm) R(cm) | zones(LCR) | DETECTION");
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < SAMPLE_MS) return;
  lastSample = now;

  bool warm = (now - bootTime) >= WARMUP_MS;

  // ---- PIR ----
  bool pirRaw = digitalRead(PIR_PIN) == HIGH;
  if (!warm) pirRaw = false;            // mask warm-up garbage
  bool pirChanged = pir.update(pirRaw);

  // ---- 3 ultrasonics (sequential, anti-cross-talk gap) ----
  float dist[3];
  bool  anyZone = false;
  for (int i = 0; i < NUM_US; i++) {
    dist[i] = readDistanceCm(US_TRIG[i], US_ECHO[i]);
    bool inZoneRaw = (dist[i] > 0 && dist[i] <= ZONE_CM);
    if (usZone[i].update(inZoneRaw))
      Serial.printf("   >> ZONE %-6s confirmed -> %s\n",
                    US_NAME[i], usZone[i].state ? "IN ZONE" : "clear");
    if (usZone[i].state) anyZone = true;
    delay(5);
  }

  // ---- A "detection" = PIR or ANY ultrasonic confirmed. This is what bumps
  //      arousal in the state machine (replacing the keyboard 't'). ----
  bool detection = pir.state || anyZone;

  // print row
  Serial.print(pir.state ? "Y" : ".");
  Serial.print("   | ");
  for (int i = 0; i < NUM_US; i++) {
    if (dist[i] > 0) Serial.print(dist[i], 1); else Serial.print("---");
    Serial.print(" ");
  }
  Serial.print("| ");
  for (int i = 0; i < NUM_US; i++) Serial.print(usZone[i].state ? "Y" : ".");
  Serial.print("       | ");
  Serial.println(detection ? "*** DETECTION ***" : "");

  if (pirChanged) Serial.printf("   >> PIR confirmed -> %s\n", pir.state ? "MOTION" : "clear");
  if (!warm && (now / 1000) % 5 == 0) Serial.println("   (PIR still warming up)");
}

// Fire a 10us trigger, time the echo, convert to cm. Returns <=0 on no echo.
float readDistanceCm(int trig, int echo) {
  digitalWrite(trig, LOW);  delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10);
  digitalWrite(trig, LOW);
  unsigned long dur = pulseIn(echo, HIGH, ECHO_TIMEOUT_US);
  if (dur == 0) return -1.0;
  return dur * 0.0343 / 2.0;    // speed of sound, round-trip
}
