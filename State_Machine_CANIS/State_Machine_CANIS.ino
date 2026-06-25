/*
  CANIS — State Machine Skeleton (ESP32)
  ---------------------------------------
  Pure logic. No sensors or actuators wired yet — every action is a
  Serial.print placeholder. Debug the brain before anything is attached.

  Serial Monitor @ 115200 baud:
    t  = simulate a bad-behavior detection (PIR/ultrasonic hit) -> bumps arousal
    r  = reset arousal to 0 (everything went quiet)

  Tap 't' a few times to climb CALM -> AROUSAL -> CORRECTION, then stop
  and watch it decay back down. The drop into CALM = "dog left the zone" = reward.
*/

// ---------- Tunables (ALL empirical — tune against real Shifu later) ----------
const float AROUSAL_MAX      = 100.0;   // ceiling
const float DECAY_PER_SEC    = 12.0;    // how fast arousal bleeds off when quiet
const float TRIGGER_BUMP     = 25.0;    // how much one detection adds

const float AROUSAL_ENTER    = 20.0;    // CALM -> AROUSAL above this
const float CORRECTION_ENTER = 70.0;    // AROUSAL -> CORRECTION above this

const unsigned long CALM_REWARD_MS = 15000;  // reward interval for sustained calm

// ---------- State ----------
enum State { CALM, AROUSAL, CORRECTION };
State state = CALM, prevState = CALM;

float arousal = 0.0;
unsigned long lastTick = 0, calmSince = 0, lastCalmReward = 0;
unsigned long lastCorrectionAction = 0, lastLog = 0;

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nCANIS state machine online.");
  Serial.println("Keys:  t = trigger (bad behavior)   r = reset to calm\n");
  lastTick = millis();
  calmSince = millis();
}

void loop() {
  unsigned long now = millis();
  float dt = (now - lastTick) / 1000.0;   // seconds since last loop
  lastTick = now;

  handleInput();          // test keys -> may bump arousal (stands in for sensors)
  decayArousal(dt);       // bleed arousal toward 0
  updateState();          // recompute state + handle transitions
  runStateBehavior(now);  // act on current state
  logStatus(now);         // 1 Hz heartbeat

  delay(20);              // ~50 Hz loop; plenty for logic testing
}

// ---------- Inputs (placeholder for real sensors — see Phase 4) ----------
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
  // LATER: replace with confirmed PIR/ultrasonic reads (require N consecutive
  // positives before bumping, so curtains/fans/shadows don't false-trigger).
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

    // Core of the design: dropping back into CALM means the dog disengaged /
    // left the zone. Reward LEAVING — never punish entering.
    if (state == CALM && (prevState == AROUSAL || prevState == CORRECTION)) {
      reward("settled / left zone");
      lastCalmReward = millis();
    }
    if (state == CALM) calmSince = millis();
  }
}

void runStateBehavior(unsigned long now) {
  switch (state) {
    case CALM:
      // Baseline reinforcement for staying calm.
      if (now - calmSince >= CALM_REWARD_MS && now - lastCalmReward >= CALM_REWARD_MS) {
        reward("sustained calm");
        lastCalmReward = now;
      }
      break;

    case AROUSAL:
      // Watch-and-wait. No intervention — just monitoring which way it goes.
      break;

    case CORRECTION:
      // Bad behavior confirmed. Intervene at intervals to get attention so the
      // dog can disengage; the settle-back into CALM is what gets rewarded.
      if (now - lastCorrectionAction >= 2000) {  // every 2s, not every loop
        lastCorrectionAction = now;
        Serial.println("  [OUTPUT] would arm marker buzzer + orient toward zone");
        // LATER: real buzzer + move-toward-zone command over serial / ESP-NOW.
      }
      break;
  }
}

void reward(const char* why) {
  Serial.print("  [OUTPUT] >>> REWARD (marker buzzer + dispense treat) -- ");
  Serial.println(why);
  // LATER: send DISPENSE to the Arduino + fire the marker buzzer.
}

void logStatus(unsigned long now) {
  if (now - lastLog >= 1000) {
    lastLog = now;
    Serial.print("["); Serial.print(name(state));
    Serial.print("] arousal="); Serial.println(arousal, 1);
  }
}

const char* name(State s) {
  switch (s) { case CALM: return "CALM"; case AROUSAL: return "AROUSAL"; case CORRECTION: return "CORRECTION"; }
  return "?";
}