/*
  CANIS — Sensor test (ESP32)   [Phase 4]
  ---------------------------------------
  Standalone. Proves the PIR + ultrasonic give CLEAN, CONFIRMED readings before
  they get wired into the state machine. No WiFi, no dashboard — just sensors.

  The point of this phase isn't the wiring, it's the CONFIRMATION LOGIC: a single
  positive blip (a curtain, a fan, a shadow, a stray echo) must NOT count. We
  require N consecutive agreeing reads before flipping "confirmed" — hysteresis
  that kills false triggers. Watch the RAW vs CONFIRMED columns to see it work.

  WIRING (ESP32 dev board):
    PIR  VCC  -> 5V (VIN)        PIR  GND -> GND        PIR  OUT -> GPIO 27 (3.3V safe)
    SR04 VCC  -> 5V (VIN)        SR04 GND -> GND
    SR04 TRIG -> GPIO 26
    SR04 ECHO -> [ 1k ]-> GPIO 25 -> [ 2k ]-> GND   <-- divider! ECHO is 5V, ESP32 is 3.3V
                       (tap between the resistors goes to GPIO 25)

  PIR module: set the jumper to "H" (repeat trigger). Give it ~60s to warm up
  after power-on; early reads are garbage and the code masks them.

  Serial Monitor @ 115200.
*/

// ---------- Pins ----------
const int PIR_PIN  = 27;   // PIR OUT (3.3V logic, read directly)
const int TRIG_PIN = 26;   // ultrasonic trigger
const int ECHO_PIN = 25;   // ultrasonic echo (THROUGH the divider)

// ---------- Tunables ----------
const int   CONFIRM_N   = 3;      // consecutive agreeing reads to flip confirmed
const int   SAMPLE_MS   = 100;    // 10 Hz sampling -> 3 reads = 300ms to confirm
const float ZONE_CM     = 30.0;   // "in the forbidden zone" if closer than this
const unsigned long WARMUP_MS = 60000;  // ignore PIR for the first 60s

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

Confirmed pir;       // confirmed motion
Confirmed zone;      // confirmed "something is in the zone"

unsigned long bootTime = 0, lastSample = 0;

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PIR_PIN, INPUT);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);
  bootTime = millis();
  Serial.println("\nCANIS sensor test. PIR warming up (~60s)...");
  Serial.println("cols:  PIRraw PIRok | dist(cm) ZONEraw ZONEok | DETECTION");
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

  // ---- Ultrasonic ----
  float dist = readDistanceCm();
  bool inZoneRaw = (dist > 0 && dist <= ZONE_CM);   // dist<=0 means no/invalid echo
  bool zoneChanged = zone.update(inZoneRaw);

  // ---- A "detection" = either sensor confirmed. This is what will bump
  //      arousal in the state machine later (replacing the keyboard 't'). ----
  bool detection = pir.state || zone.state;

  // print
  Serial.print(pirRaw); Serial.print("      ");
  Serial.print(pir.state ? "Y" : "."); Serial.print("    | ");
  if (dist > 0) Serial.print(dist, 1); else Serial.print("--- ");
  Serial.print("   "); Serial.print(inZoneRaw); Serial.print("     ");
  Serial.print(zone.state ? "Y" : "."); Serial.print("   | ");
  Serial.println(detection ? "*** DETECTION ***" : "");

  if (pirChanged)  Serial.printf("   >> PIR confirmed -> %s\n",  pir.state  ? "MOTION" : "clear");
  if (zoneChanged) Serial.printf("   >> ZONE confirmed -> %s\n", zone.state ? "IN ZONE" : "clear");
  if (!warm && (now / 1000) % 5 == 0) Serial.println("   (PIR still warming up)");
}

// Fire a 10us trigger, time the echo, convert to cm. Returns <=0 on no echo.
float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // timeout ~25ms ~= 4m max range; returns 0 if no echo in time
  unsigned long dur = pulseIn(ECHO_PIN, HIGH, 25000);
  if (dur == 0) return -1.0;
  return dur * 0.0343 / 2.0;    // speed of sound, round-trip
}
