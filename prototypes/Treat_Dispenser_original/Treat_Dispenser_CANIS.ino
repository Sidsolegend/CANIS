/*
  CANIS - Limbs (Arduino Uno): treat dispenser + L298N motors
  -----------------------------------------------------------
  The Uno is the dumb, reliable executor. The ESP32 (brain) sends newline-
  terminated commands over a one-way SoftwareSerial link; the Uno acts on them
  with precise real-time timing.

  COMMANDS understood over the link (and by the Serial Monitor for bench testing):
    DISPENSE   marker buzzer -> servo gate opens briefly -> closes (a reward)
    FORWARD    both wheels forward
    BACKWARD   both wheels reverse
    LEFT       pivot left   (left wheel reverse, right wheel forward)
    RIGHT      pivot right  (right wheel reverse, left wheel forward)
    STOP       both wheels stop

  DISPENSE trigger #2: the push-button (pin 2) still works standalone.

  ESP32 link wiring (one-way, ESP32 -> Arduino):
    ESP32 GPIO 13 (TX2) ---> Arduino pin 10 (SoftwareSerial RX)
    ESP32 GND           ---> Arduino GND   (shared ground is required)
  3.3V from the ESP32 into the Uno's 5V RX reads fine - no level shifter needed
  in this direction.

  SAFETY WATCHDOG (important): movement is NOT latched. A move command drives the
  motors for at most MOTION_TIMEOUT_MS, then auto-STOPs unless a fresh command
  arrives. So if the serial link drops mid-move, the robot halts within ~1s
  instead of running away - Shifu is afraid of moving objects, so a runaway is
  the worst possible failure. The brain must RE-SEND the move command (e.g. every
  ~500 ms) to keep moving.

  L298N power: the motor supply (+12V terminal) comes from its OWN battery/
  power-bank, NEVER the Arduino 5V. Grounds are common across everything.

  TIMER NOTE (Uno): tone() on pin 6 and PWM (analogWrite) on ENA pin 3 both use
  Timer2 - while the 120 ms marker tone plays during a DISPENSE, ENA PWM is
  briefly disrupted. Harmless because rewards happen while settled/stopped, but
  the firmware also STOPs the motors at the start of every dispense to be safe.
*/

#include <Servo.h>
#include <SoftwareSerial.h>

// --- Dispenser pins ---
const int SERVO_PIN  = 9;
const int BUZZER_PIN = 6;
const int BUTTON_PIN = 2;
const int LINK_RX    = 10;   // <- ESP32 GPIO 13
const int LINK_TX    = 11;   // unused (we only receive), leave unwired

// --- L298N pins (match hardware/WIRING.md) ---
const int ENA = 3;    // PWM - left motor (A) speed
const int IN1 = 4;    // left motor direction
const int IN2 = 7;    // left motor direction
const int IN3 = 8;    // right motor direction
const int IN4 = 12;   // right motor direction
const int ENB = 5;    // PWM - right motor (B) speed

// --- Servo angles (EYEBALL these against your bottle) ---
const int CLOSED_ANGLE = 80;   // flap fully covering the hole
const int OPEN_ANGLE   = 0;    // flap swung clear of the hole

// --- Dispenser timing (tune after the 20-trigger test) ---
const int MARKER_FREQ  = 2000; // marker tone, Hz
const int MARKER_MS    = 120;  // how long it beeps
const int PRE_DROP_MS  = 150;  // gap between beep and gate opening
const int GATE_OPEN_MS = 150;  // how long the flap stays open -- MAIN tuning knob

// --- Motor tuning ---
// 0-255. Set higher for the first on-floor bring-up test so the loaded chassis
// actually moves at 6V (the L298N eats ~1.5-2V, so motors see ~4V). Once driving
// is proven, dial these DOWN to the gentlest speed that still moves - CANIS
// introduces motion gradually so Shifu isn't startled. PIVOT_SPEED is a touch
// higher because turning in place needs more torque to break static friction.
int DRIVE_SPEED = 200;
int PIVOT_SPEED = 220;

// Watchdog: motors auto-stop this long after the last movement command.
const unsigned long MOTION_TIMEOUT_MS = 1000;

Servo gate;
SoftwareSerial link(LINK_RX, LINK_TX);   // RX, TX

int  lastButtonState = HIGH;   // INPUT_PULLUP idle = HIGH
int  dispenseCount   = 0;
char cmdBuf[16];
int  cmdLen = 0;

bool          moving        = false;   // are the motors currently driven?
unsigned long lastMoveCmdMs = 0;       // millis() of the last movement command

void setup() {
  Serial.begin(9600);
  link.begin(9600);

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  pinMode(ENA, OUTPUT); pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(ENB, OUTPUT); pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);
  stopMotors();                 // start halted

  gate.attach(SERVO_PIN);
  gate.write(CLOSED_ANGLE);     // start closed

  Serial.println("CANIS limbs ready. DISPENSE / FORWARD / BACKWARD / LEFT / RIGHT / STOP.");
}

void loop() {
  // --- button trigger (falling edge: HIGH -> LOW means just pressed) ---
  int buttonState = digitalRead(BUTTON_PIN);
  if (lastButtonState == HIGH && buttonState == LOW) {
    dispense("button");
    delay(50);                  // debounce
  }
  lastButtonState = buttonState;

  // --- command from ESP32 over the link (newline-terminated) ---
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

  // --- also accept commands typed into the Serial Monitor (bench testing) ---
  static char monBuf[16];
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

  // --- safety watchdog: stop if no fresh move command in time ---
  if (moving && (millis() - lastMoveCmdMs > MOTION_TIMEOUT_MS)) {
    stopMotors();
    Serial.println("[watchdog] no move command -> STOP");
  }
}

void handleCommand(const char* cmd) {
  if      (strcmp(cmd, "DISPENSE") == 0) dispense("link");
  else if (strcmp(cmd, "FORWARD")  == 0) drive( DRIVE_SPEED,  DRIVE_SPEED, "FORWARD");
  else if (strcmp(cmd, "BACKWARD") == 0) drive(-DRIVE_SPEED, -DRIVE_SPEED, "BACKWARD");
  else if (strcmp(cmd, "LEFT")     == 0) drive(-PIVOT_SPEED,  PIVOT_SPEED, "LEFT");
  else if (strcmp(cmd, "RIGHT")    == 0) drive( PIVOT_SPEED, -PIVOT_SPEED, "RIGHT");
  else if (strcmp(cmd, "STOP")     == 0) stopMotors();
  // unknown commands are ignored on purpose
}

// Signed speeds: + = forward, - = reverse, 0 = stop. left = motor A, right = B.
void drive(int left, int right, const char* label) {
  setMotor(IN1, IN2, ENA, left);
  setMotor(IN3, IN4, ENB, right);
  moving = true;
  lastMoveCmdMs = millis();
  Serial.print("[move] "); Serial.println(label);
}

// One motor channel. Flip the OUT wires (or swap inA/inB here) if a wheel
// spins the wrong way relative to "forward".
void setMotor(int inA, int inB, int en, int speed) {
  if (speed > 0) {            // forward
    digitalWrite(inA, HIGH);
    digitalWrite(inB, LOW);
  } else if (speed < 0) {     // reverse
    digitalWrite(inA, LOW);
    digitalWrite(inB, HIGH);
  } else {                    // coast/stop
    digitalWrite(inA, LOW);
    digitalWrite(inB, LOW);
  }
  int mag = speed < 0 ? -speed : speed;
  if (mag > 255) mag = 255;
  analogWrite(en, mag);
}

void stopMotors() {
  setMotor(IN1, IN2, ENA, 0);
  setMotor(IN3, IN4, ENB, 0);
  moving = false;
}

void dispense(const char* source) {
  stopMotors();               // settle before rewarding (also frees Timer2 for tone())
  dispenseCount++;
  Serial.print("Dispense #");
  Serial.print(dispenseCount);
  Serial.print(" (");
  Serial.print(source);
  Serial.println(")");

  tone(BUZZER_PIN, MARKER_FREQ, MARKER_MS);  // marker
  delay(PRE_DROP_MS);

  gate.write(OPEN_ANGLE);
  delay(GATE_OPEN_MS);
  gate.write(CLOSED_ANGLE);
}
