/*
  CANIS — Limbs (Arduino Uno)  [OPTIMIZED DUPLICATE]
  ==================================================
  Identical to the original Treat_Dispenser_CANIS/Treat_Dispenser_CANIS.ino,
  plus two review fixes:

    [OPT-7] Dispense-ACK. After a treat drop fully completes (gate opened AND
            closed), the Uno emits "ACK <id>\n" back to the ESP32 over the link's
            TX line. The ESP32 increments reward_count only on that ACK, so the
            dashboard's reward number reflects DELIVERY, not intent — a jam,
            empty hopper, or dropped command no longer inflates the count.
            (orig dispense() ended silently, Treat_Dispenser_CANIS.ino:193-208)

    [C5]    Correlation id (TECH_DEBT #4 / ROADMAP C5). A dispense triggered by
            the link carries an id — "DISPENSE <n>" — which the Uno echoes back
            as "ACK <n>" / "JAM <n>" so the ESP32 can tell WHICH physical drop a
            reply belongs to. A LOCAL button-press dispense is not ESP32-
            requested, so it replies with the sentinel id 0 ("ACK 0" / "JAM 0"):
            if it lands while the ESP32 is waiting on its own reward id, the id
            won't match and the ESP32 discards it instead of miscounting ±1. A
            bare "DISPENSE" (e.g. typed on this Uno's Serial Monitor) parses to
            id 0 too, so older senders still work.

    [OPT-11] Jam detection (current-less: expected-beam-break timeout).
            An IR beam-break pair (emitter + phototransistor receiver, e.g. an
            Adafruit 2167-style 3 mm pair or a slotted opto-interrupter) sits
            across the CHUTE EXIT. A treat that actually falls interrupts the
            beam for a few ms. After the gate cycles, the sketch waits up to
            DROP_TIMEOUT_MS for that interruption:
              beam broke   -> the treat verifiably left the chute -> "ACK"
              no break     -> re-open the gate once (JAM_RETRY), wait again
              still nothing-> report "JAM" instead of ACK — the ESP32 stops
                              counting AND (after repeated jams) stops asking.
            No motor-current sensing, no extra shield: one digital input.
            Guarded by JAM_SENSOR_ENABLED (default 0 = behave exactly like the
            plain [OPT-7] sketch until the sensor is physically installed).

    [C7]    Persisted commissioning calibration (ROADMAP C7). Two bring-up steps
            that USED to mean physically redoing the assembly are now one-time
            software fixes saved in this Uno's EEPROM and applied on every boot:
              - MOTOR direction. A wheel that spins the wrong way no longer means
                "swap that motor's two OUT leads" (hardware/TESTING.md:28). Each
                drive motor has a persisted invert flag that setMotor() XORs into
                the direction sign; INVL / INVR toggle + save it.
              - Dispenser flap ZERO. The closed/open angle no longer depends on
                exactly how the flap was pressed onto the servo horn
                (hardware/BUILD_GUIDE_v2.md:83). A persisted trim offset (+/-15
                deg, clamped) shifts CLOSED_ANGLE/OPEN_ANGLE; TRIM+ / TRIM- nudge
                + save it. CAL? prints the live calibration.
            These commands are word-style (like DISPENSE/FORWARD) so they work
            both from this Uno's own Serial Monitor AND when the ESP32 sends them
            over the link during its guided 'c' self-test (CANIS_Core.ino [C7]).
            MOTL / MOTR pulse ONE motor forward (honoring its invert flag) so the
            self-test can check each direction in isolation.
            NOTE: the flags MUST live here, not on the ESP32 — setMotor() and the
            servo run on this Uno, and the Uno's persistence primitive is EEPROM
            (Preferences is ESP32-only). The ESP32 self-test drives them remotely.

    [MOBILE] Differential-drive executor verb (docs/INTERFACE_CONTRACT.md).
            NEW command on the existing 9600 link grammar:
              "VEL <pwmL> <pwmR>\n"    pwm in [-255, 255], signed = direction
            The ESP32 owns ALL the smarts (odometry, CBF/QP, wheel mapping
            v_L = v - omega*L/2 / v_R = v + omega*L/2, per-wheel PI at
            CANIS_CONTROL_RATE_HZ) and hands this Uno nothing but two final
            signed PWM duties. This sketch stays a DUMB EXECUTOR on purpose:
              - VEL drives both L298N channels through the SAME setMotor()
                path as FORWARD/LEFT/etc, honoring the persisted [C7] invert
                flags, and feeds the SAME MOTION_TIMEOUT_MS(1000) watchdog.
                The watchdog is supreme: if VEL (or any motion verb) stops
                arriving — ESP32 crash, WiFi loss, cut wire — motors stop
                within ~1s with no ESP32 cooperation required. Unchanged.
              - A malformed VEL (missing/garbled numbers) is IGNORED: it does
                not touch the motors and does NOT feed the watchdog, so a
                corrupted stream decays to STOP instead of acting on noise.
              - Stops arrive as the explicit "STOP" verb (contract LAW: the
                ESP32 never sends "VEL 0 0" to stop); if a "VEL 0 0" ever
                does arrive it simply drives zero PWM — harmless.
              - Nothing here EVER initiates motion: no command, no movement
                (the [C7] MOTL/MOTR pulses are self-limiting bench verbs).
            An OLD ESP32 that never sends VEL leaves this path dormant; the
            word verbs above behave exactly as before.

  WIRING for ACK (the return line the original left unwired):
    Arduino pin 11 (SoftwareSerial TX) --> ESP32 GPIO 4 (RX2)
  This is a 5V TX into a 3.3V input, so it MUST pass through a level shifter or
  a resistor divider (e.g. 1k top / 2k bottom gives ~3.3V). Do NOT wire 5V
  straight to the ESP32 pin. If you can't add the return line, set
  CANIS_USE_DISPENSE_ACK to 0 on the ESP32 side and leave pin 11 unwired — this
  sketch still runs (it just talks to nobody on TX).

  WIRING for the beam-break (only when JAM_SENSOR_ENABLED = 1):
    emitter LED:   5V -> emitter anode, cathode -> 220R -> GND (always on)
    receiver OUT -> Arduino A0 (used as a digital input, INPUT_PULLUP)
    receiver VCC/GND -> 5V/GND
  Typical receivers pull OUT LOW while the beam is INTACT... or the inverse,
  depending on the part — set BEAM_BROKEN_LEVEL to match yours (verify with the
  Serial Monitor: wave a treat through and watch the "[beam]" prints).

  Everything else is verbatim from the original.
*/

#include <Servo.h>
#include <SoftwareSerial.h>
#include <EEPROM.h>          // [C7] persist motor-invert + servo-trim calibration

// --- Dispenser pins ---
const int SERVO_PIN  = 9;
const int BUZZER_PIN = 6;
const int BUTTON_PIN = 2;
const int LINK_RX    = 10;   // <- ESP32 GPIO 13
const int LINK_TX    = 11;   // [OPT-7] now WIRED -> ESP32 GPIO 4 (via level shift) for ACK

// --- L298N pins ---
const int ENA = 3;
const int IN1 = 4;
const int IN2 = 7;
const int IN3 = 8;
const int IN4 = 12;
const int ENB = 5;

// --- Servo angles ---
const int CLOSED_ANGLE = 120;  // [bench 2026-09] new external servo mount, flap re-seated on the horn: 120 covers the gate hole
const int OPEN_ANGLE   = 40;   // ...and 40 swings it clear (same 80 deg swing; the original mount was 80 closed / 0 open)

// --- Dispenser timing ---
const int MARKER_FREQ  = 2000;
const int MARKER_MS    = 120;
const int PRE_DROP_MS  = 150;
const int GATE_OPEN_MS = 150;

// --- Jam detection [OPT-11] ---
// Set JAM_SENSOR_ENABLED to 1 once the IR beam-break pair is installed across
// the chute exit. With 0 (default) this sketch behaves exactly like [OPT-7]:
// ACK after the gate cycle, no verification.
#define JAM_SENSOR_ENABLED 0
const int  BEAM_PIN          = A0;    // receiver OUT (digital read, INPUT_PULLUP)
const int  BEAM_BROKEN_LEVEL = LOW;   // logic level on BEAM_PIN when a treat interrupts the beam
const unsigned long DROP_TIMEOUT_MS = 600;  // max wait for the falling treat to break the beam
const int  JAM_RETRY         = 1;     // extra gate cycles to try before declaring JAM
const int  RETRY_EXTRA_MS    = 100;   // hold the gate open this much longer on a retry

// --- Motor tuning ---
int DRIVE_SPEED = 200;
int PIVOT_SPEED = 220;

const unsigned long MOTION_TIMEOUT_MS = 1000;

// --- Commissioning calibration [C7] (persisted in EEPROM) ---
// Saved once at bring-up, re-applied on every boot, so the two "redo the
// assembly" fixes become one-time software fixes. TRIM_MAX_DEG bounds how far the
// flap zero can be shifted so a bad TRIM+ spam can't drive the servo into a stall.
const int TRIM_MAX_DEG  = 15;   // servo-trim clamp: |TRIM_OFFSET| may not exceed this
const int TRIM_STEP_DEG = 5;    // one TRIM+ / TRIM- nudge

// EEPROM image. magic+ver gate against an uninitialized (0xFF) or stale layout.
struct Calib { uint8_t magic; uint8_t ver; uint8_t lInv; uint8_t rInv; int8_t trim; };
const uint8_t CALIB_MAGIC = 0xCA;
const uint8_t CALIB_VER   = 1;
const int     CALIB_ADDR  = 0;

bool MOTOR_L_INVERT = false;   // XORed into the LEFT  motor's direction sign
bool MOTOR_R_INVERT = false;   // XORed into the RIGHT motor's direction sign
int  TRIM_OFFSET   = 0;       // degrees added to CLOSED_ANGLE/OPEN_ANGLE (clamped)

Servo gate;
SoftwareSerial link(LINK_RX, LINK_TX);

int  lastButtonState = HIGH;
int  dispenseCount   = 0;
char cmdBuf[20];     // [MOBILE] sized for the longest verb "VEL -255 -255" (13 chars) + margin
int  cmdLen = 0;

bool          moving        = false;
unsigned long lastMoveCmdMs = 0;

void setup() {
  Serial.begin(9600);
  link.begin(9600);

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
#if JAM_SENSOR_ENABLED
  pinMode(BEAM_PIN, INPUT_PULLUP);    // [OPT-11] beam-break receiver
#endif

  pinMode(ENA, OUTPUT); pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(ENB, OUTPUT); pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);

  loadCalibration();     // [C7] restore persisted motor-invert + servo-trim
  stopMotors();

  gate.attach(SERVO_PIN);
  gate.write(closedAngle());   // [C7] honor the persisted flap-zero trim

  Serial.println("CANIS limbs ready (optimized). DISPENSE / FORWARD / BACKWARD / LEFT / RIGHT / STOP / VEL <l> <r>.");
  Serial.println("Calib [C7]: MOTL/MOTR pulse a motor · INVL/INVR flip a motor · TRIM+/TRIM- flap zero · CAL? show");
  reportCalibration();
}

// ---------- Persisted calibration [C7] ----------
// The flags/trim survive a reboot, so a motor lead-swap or a servo-horn re-press
// is replaced by a one-time serial command. EEPROM.put only writes changed bytes.
void loadCalibration() {
  Calib c;
  EEPROM.get(CALIB_ADDR, c);
  if (c.magic != CALIB_MAGIC || c.ver != CALIB_VER) {
    // Uninitialized (fresh AVR reads 0xFF) or stale layout -> defaults, then save.
    MOTOR_L_INVERT = false; MOTOR_R_INVERT = false; TRIM_OFFSET = 0;
    saveCalibration();
    Serial.println("[C7] no saved calibration -> defaults written");
    return;
  }
  MOTOR_L_INVERT = c.lInv != 0;
  MOTOR_R_INVERT = c.rInv != 0;
  TRIM_OFFSET   = clampTrim(c.trim);   // defend against a bad stored value
}

void saveCalibration() {
  Calib c = { CALIB_MAGIC, CALIB_VER,
              (uint8_t)(MOTOR_L_INVERT ? 1 : 0), (uint8_t)(MOTOR_R_INVERT ? 1 : 0),
              (int8_t)clampTrim(TRIM_OFFSET) };
  EEPROM.put(CALIB_ADDR, c);
}

int clampTrim(int v) {
  if (v >  TRIM_MAX_DEG) return  TRIM_MAX_DEG;
  if (v < -TRIM_MAX_DEG) return -TRIM_MAX_DEG;
  return v;
}

// Trimmed + range-limited servo angles. constrain() to [0,180] so no trim can
// ever command the SG90 past its mechanical limits.
int closedAngle() { return constrain(CLOSED_ANGLE + TRIM_OFFSET, 0, 180); }
int openAngle()   { return constrain(OPEN_ANGLE   + TRIM_OFFSET, 0, 180); }

void reportCalibration() {
  Serial.print("[C7] calib: L_invert="); Serial.print(MOTOR_L_INVERT ? "ON" : "off");
  Serial.print("  R_invert="); Serial.print(MOTOR_R_INVERT ? "ON" : "off");
  Serial.print("  trim="); Serial.print(TRIM_OFFSET);
  Serial.print(" (closed="); Serial.print(closedAngle());
  Serial.print(" open="); Serial.print(openAngle()); Serial.println(")");
}

void loop() {
  int buttonState = digitalRead(BUTTON_PIN);
  if (lastButtonState == HIGH && buttonState == LOW) {
    dispense("button", 0);   // [C5] local trigger -> sentinel id 0 (not ESP32-requested)
    delay(50);
  }
  lastButtonState = buttonState;

  while (link.available()) {
    char c = link.read();
    if (c == '\n' || c == '\r') {
      cmdBuf[cmdLen] = '\0';
      handleCommand(cmdBuf);
      cmdLen = 0;
    } else if (cmdLen < (int)sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }

  static char monBuf[20];   // [MOBILE] match cmdBuf so a hand-typed VEL works from the Monitor
  static int  monLen = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      monBuf[monLen] = '\0';
      if (monLen) handleCommand(monBuf);
      monLen = 0;
    } else if (monLen < (int)sizeof(monBuf) - 1) {
      monBuf[monLen++] = c;
    }
  }

  if (moving && (millis() - lastMoveCmdMs > MOTION_TIMEOUT_MS)) {
    stopMotors();
    Serial.println("[watchdog] no move command -> STOP");
  }
}

void handleCommand(const char* cmd) {
  // [C5] "DISPENSE" or "DISPENSE <n>". atoi() of the tail gives the correlation
  // id to echo back; a bare "DISPENSE" parses to 0 (sentinel) so older senders
  // and a hand-typed Serial Monitor command still work.
  if      (strncmp(cmd, "DISPENSE", 8) == 0 && (cmd[8] == '\0' || cmd[8] == ' '))
                                         dispense("link", atoi(cmd + 8));
  else if (strcmp(cmd, "FORWARD")  == 0) drive( DRIVE_SPEED,  DRIVE_SPEED, "FORWARD");
  else if (strcmp(cmd, "BACKWARD") == 0) drive(-DRIVE_SPEED, -DRIVE_SPEED, "BACKWARD");
  else if (strcmp(cmd, "LEFT")     == 0) drive(-PIVOT_SPEED,  PIVOT_SPEED, "LEFT");
  else if (strcmp(cmd, "RIGHT")    == 0) drive( PIVOT_SPEED, -PIVOT_SPEED, "RIGHT");
  else if (strcmp(cmd, "STOP")     == 0) stopMotors();
  // [bench 2026-09] "BEEP <hz> <ms>" — the ESP32 has no buzzer of its own in this build, so it asks
  // this Uno to play its GUARD-mode correction cue on the pin-6 buzzer.
  else if (strncmp(cmd, "BEEP", 4) == 0 && cmd[4] == ' ') {
    int hz = 0, ms = 0;
    if (sscanf(cmd + 5, "%d %d", &hz, &ms) == 2 && hz >= 100 && hz <= 5000 && ms > 0 && ms <= 2000)
      tone(BUZZER_PIN, hz, ms);
  }
  // [bench 2026-09] "SERVO <deg>" — calibration aid: park the servo at an exact angle (0-180) so the
  // real CLOSED/OPEN angles for this mount can be found by eye. Not used by the normal flow.
  else if (strncmp(cmd, "SERVO", 5) == 0 && cmd[5] == ' ') {
    int deg = atoi(cmd + 6);
    if (deg >= 0 && deg <= 180) { gate.write(deg); Serial.print("[cal] servo -> "); Serial.println(deg); }
  }
  // [MOBILE] "VEL <pwmL> <pwmR>" — per-wheel signed PWM from the ESP32's CBF
  // pipeline. Same executor path + same watchdog as the word verbs above.
  else if (strncmp(cmd, "VEL", 3)  == 0 && cmd[3] == ' ') velCommand(cmd + 4);
  // [C7] Commissioning commands (word-style, like the movement verbs above). Sent
  // by the ESP32's 'c' self-test over the link, or typed on this Uno's Monitor.
  else if (strcmp(cmd, "MOTL")     == 0) pulseMotor(IN1, IN2, ENA, MOTOR_L_INVERT, "LEFT");
  else if (strcmp(cmd, "MOTR")     == 0) pulseMotor(IN3, IN4, ENB, MOTOR_R_INVERT, "RIGHT");
  else if (strcmp(cmd, "INVL")     == 0) { MOTOR_L_INVERT = !MOTOR_L_INVERT; saveCalibration();
                                           Serial.print("[C7] LEFT invert -> ");  Serial.println(MOTOR_L_INVERT ? "ON (saved)" : "off (saved)"); }
  else if (strcmp(cmd, "INVR")     == 0) { MOTOR_R_INVERT = !MOTOR_R_INVERT; saveCalibration();
                                           Serial.print("[C7] RIGHT invert -> "); Serial.println(MOTOR_R_INVERT ? "ON (saved)" : "off (saved)"); }
  else if (strcmp(cmd, "TRIM+")    == 0) nudgeTrim( TRIM_STEP_DEG);
  else if (strcmp(cmd, "TRIM-")    == 0) nudgeTrim(-TRIM_STEP_DEG);
  else if (strcmp(cmd, "CAL?")     == 0) reportCalibration();
}

// [MOBILE] Parse and execute "VEL <pwmL> <pwmR>". args points past "VEL ".
// ASSUMPTIONS (docs/INTERFACE_CONTRACT.md):
//   - pwmL/pwmR are final per-wheel duties in [-255,255]; ALL kinematics,
//     odometry-based PI correction, and CBF safety clamping already happened
//     on the ESP32 at CANIS_CONTROL_RATE_HZ. This Uno adds NO control law.
//   - The ESP32 re-sends within CANIS_MOVE_PULSE_MS(500ms) while moving, so
//     feeding lastMoveCmdMs here keeps the unchanged MOTION_TIMEOUT_MS(1000)
//     watchdog satisfied; a silent link -> auto-STOP within ~1s (supreme).
//   - Stops come as the explicit "STOP" verb, never "VEL 0 0".
// A malformed line (either number missing) is ignored WITHOUT feeding the
// watchdog — noise on the link decays to STOP rather than becoming motion.
// Out-of-range values are clamped, not rejected: by the time a value reaches
// the wire it was already clamped upstream, so a stray digit should saturate
// safely instead of stranding the robot mid-leg with a stale command.
void velCommand(const char* args) {
  char* end;
  long l = strtol(args, &end, 10);
  if (end == args) { Serial.println("[vel] malformed (L) -> ignored"); return; }
  const char* rest = end;
  long r = strtol(rest, &end, 10);
  if (end == rest) { Serial.println("[vel] malformed (R) -> ignored"); return; }
  l = constrain(l, -255, 255);
  r = constrain(r, -255, 255);

  // [C7] invert flags apply here exactly as in drive(): a wrong-way wheel is
  // still a one-time INVL/INVR fix, not a wiring change.
  setMotor(IN1, IN2, ENA, (int)l, MOTOR_L_INVERT);
  setMotor(IN3, IN4, ENB, (int)r, MOTOR_R_INVERT);
  moving = true;
  lastMoveCmdMs = millis();

  // Print only on CHANGE: at the ESP32's control rate a per-command print
  // would eat milliseconds per line at 9600 and skew the executor timing.
  static long lastL = 0, lastR = 0;
  static bool everPrinted = false;
  if (!everPrinted || l != lastL || r != lastR) {
    everPrinted = true; lastL = l; lastR = r;
    Serial.print("[vel] L="); Serial.print(l);
    Serial.print(" R=");      Serial.println(r);
  }
}

void drive(int left, int right, const char* label) {
  // [C7] Each motor gets its persisted invert flag; a wrong-way wheel is fixed
  // once with INVL/INVR instead of physically swapping that motor's OUT leads.
  setMotor(IN1, IN2, ENA, left,  MOTOR_L_INVERT);
  setMotor(IN3, IN4, ENB, right, MOTOR_R_INVERT);
  moving = true;
  lastMoveCmdMs = millis();
  Serial.print("[move] "); Serial.println(label);
}

// [C7] pulse ONE motor forward (respecting its invert flag), then stop. Lets the
// ESP32 self-test check each wheel's direction in isolation. Self-limiting delay,
// so it never engages the movement watchdog path.
void pulseMotor(int inA, int inB, int en, bool invert, const char* name) {
  stopMotors();
  Serial.print("[C7] pulse "); Serial.print(name); Serial.println(" motor forward (500ms)");
  setMotor(inA, inB, en, DRIVE_SPEED, invert);
  delay(500);
  setMotor(inA, inB, en, 0, invert);
}

// [C7] nudge the flap-zero trim by one step, clamp to +/-TRIM_MAX_DEG, persist,
// and re-seat the gate at the new closed position so the change is visible.
void nudgeTrim(int delta) {
  TRIM_OFFSET = clampTrim(TRIM_OFFSET + delta);
  saveCalibration();
  gate.write(closedAngle());
  Serial.print("[C7] trim -> "); Serial.print(TRIM_OFFSET);
  Serial.print(" (closed="); Serial.print(closedAngle());
  Serial.print(" open="); Serial.print(openAngle()); Serial.println(", saved)");
}

// [C7] setMotor gains an invert flag XORed into the direction sign. Everything
// else (sign->IN pair, magnitude clamp, PWM) is verbatim from the original.
void setMotor(int inA, int inB, int en, int speed, bool invert) {
  if (invert) speed = -speed;   // [C7] flip direction without re-wiring the motor
  if (speed > 0) {
    digitalWrite(inA, HIGH);
    digitalWrite(inB, LOW);
  } else if (speed < 0) {
    digitalWrite(inA, LOW);
    digitalWrite(inB, HIGH);
  } else {
    digitalWrite(inA, LOW);
    digitalWrite(inB, LOW);
  }
  int mag = speed < 0 ? -speed : speed;
  if (mag > 255) mag = 255;
  analogWrite(en, mag);
}

void stopMotors() {
  setMotor(IN1, IN2, ENA, 0, false);   // [C7] invert irrelevant at speed 0
  setMotor(IN3, IN4, ENB, 0, false);
  moving = false;
}

// One servo gate cycle. extraMs lets a retry hold the gate open a bit longer
// to shake a wedged treat loose.
void cycleGate(int extraMs) {
  gate.write(openAngle());        // [C7] persisted flap-zero trim, clamped to [0,180]
  delay(GATE_OPEN_MS + extraMs);
  gate.write(closedAngle());
}

#if JAM_SENSOR_ENABLED
// [OPT-11] Block up to timeoutMs watching for the falling treat to interrupt
// the beam. Returns true if a drop was seen. Blocking is fine here: motors are
// already stopped, and incoming link bytes sit in SoftwareSerial's buffer.
bool waitForDrop(unsigned long timeoutMs) {
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    if (digitalRead(BEAM_PIN) == BEAM_BROKEN_LEVEL) {
      Serial.println("[beam] drop confirmed");
      return true;
    }
  }
  return false;
}
#endif

// [C5] reqId is the correlation id to echo in the reply: the id from a
// "DISPENSE <n>" link command, or 0 for a local button-press / bare DISPENSE.
void dispense(const char* source, int reqId) {
  stopMotors();
  dispenseCount++;
  Serial.print("Dispense #");
  Serial.print(dispenseCount);
  Serial.print(" (");
  Serial.print(source);
  Serial.print(", req=");
  Serial.print(reqId);
  Serial.println(")");

  tone(BUZZER_PIN, MARKER_FREQ, MARKER_MS);
  delay(PRE_DROP_MS);

#if JAM_SENSOR_ENABLED
  // [OPT-11] Verified dispense: gate cycle -> expect a beam break within
  // DROP_TIMEOUT_MS; retry once with a longer gate hold; else report JAM.
  bool dropped = false;
  for (int attempt = 0; attempt <= JAM_RETRY && !dropped; attempt++) {
    cycleGate(attempt == 0 ? 0 : RETRY_EXTRA_MS);
    dropped = waitForDrop(DROP_TIMEOUT_MS);
    if (!dropped && attempt < JAM_RETRY)
      Serial.println("[beam] no drop seen -> retrying gate");
  }
  if (dropped) {
    replyWithId("ACK", reqId);
  } else {
    Serial.println("[beam] NO DROP after retry -> JAM");
    replyWithId("JAM", reqId);   // ESP32 counts nothing and escalates on repeats
  }
#else
  cycleGate(0);
  // [OPT-7] Confirm the completed drop so the ESP32 counts delivery, not intent.
  // Sent for every dispense (button or link); the ESP32 only acts on it when the
  // echoed id [C5] matches the reward it currently has awaiting confirmation.
  replyWithId("ACK", reqId);
#endif
}

// [C5] Emit "ACK <id>\n" / "JAM <id>\n" on the link so the ESP32 can correlate
// the reply with the exact dispense it triggered (id 0 = a local/untracked one).
void replyWithId(const char* kind, int reqId) {
  link.print(kind);
  link.print(' ');
  link.println(reqId);
}
