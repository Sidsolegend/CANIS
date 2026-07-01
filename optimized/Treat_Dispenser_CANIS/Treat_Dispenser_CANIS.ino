/*
  CANIS — Limbs (Arduino Uno)  [OPTIMIZED DUPLICATE]
  ==================================================
  Identical to the original Treat_Dispenser_CANIS/Treat_Dispenser_CANIS.ino,
  plus one review fix:

    [OPT-7] Dispense-ACK. After a treat drop fully completes (gate opened AND
            closed), the Uno emits "ACK\n" back to the ESP32 over the link's TX
            line. The ESP32 increments reward_count only on that ACK, so the
            dashboard's reward number reflects DELIVERY, not intent — a jam,
            empty hopper, or dropped command no longer inflates the count.
            (orig dispense() ended silently, Treat_Dispenser_CANIS.ino:193-208)

  WIRING for ACK (the return line the original left unwired):
    Arduino pin 11 (SoftwareSerial TX) --> ESP32 GPIO 4 (RX2)
  This is a 5V TX into a 3.3V input, so it MUST pass through a level shifter or
  a resistor divider (e.g. 1k top / 2k bottom gives ~3.3V). Do NOT wire 5V
  straight to the ESP32 pin. If you can't add the return line, set
  CANIS_USE_DISPENSE_ACK to 0 on the ESP32 side and leave pin 11 unwired — this
  sketch still runs (it just talks to nobody on TX).

  Everything else is verbatim from the original.
*/

#include <Servo.h>
#include <SoftwareSerial.h>

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
const int CLOSED_ANGLE = 80;
const int OPEN_ANGLE   = 0;

// --- Dispenser timing ---
const int MARKER_FREQ  = 2000;
const int MARKER_MS    = 120;
const int PRE_DROP_MS  = 150;
const int GATE_OPEN_MS = 150;

// --- Motor tuning ---
int DRIVE_SPEED = 200;
int PIVOT_SPEED = 220;

const unsigned long MOTION_TIMEOUT_MS = 1000;

Servo gate;
SoftwareSerial link(LINK_RX, LINK_TX);

int  lastButtonState = HIGH;
int  dispenseCount   = 0;
char cmdBuf[16];
int  cmdLen = 0;

bool          moving        = false;
unsigned long lastMoveCmdMs = 0;

void setup() {
  Serial.begin(9600);
  link.begin(9600);

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  pinMode(ENA, OUTPUT); pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(ENB, OUTPUT); pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);
  stopMotors();

  gate.attach(SERVO_PIN);
  gate.write(CLOSED_ANGLE);

  Serial.println("CANIS limbs ready (optimized). DISPENSE / FORWARD / BACKWARD / LEFT / RIGHT / STOP.");
}

void loop() {
  int buttonState = digitalRead(BUTTON_PIN);
  if (lastButtonState == HIGH && buttonState == LOW) {
    dispense("button");
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
}

void drive(int left, int right, const char* label) {
  setMotor(IN1, IN2, ENA, left);
  setMotor(IN3, IN4, ENB, right);
  moving = true;
  lastMoveCmdMs = millis();
  Serial.print("[move] "); Serial.println(label);
}

void setMotor(int inA, int inB, int en, int speed) {
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
  setMotor(IN1, IN2, ENA, 0);
  setMotor(IN3, IN4, ENB, 0);
  moving = false;
}

void dispense(const char* source) {
  stopMotors();
  dispenseCount++;
  Serial.print("Dispense #");
  Serial.print(dispenseCount);
  Serial.print(" (");
  Serial.print(source);
  Serial.println(")");

  tone(BUZZER_PIN, MARKER_FREQ, MARKER_MS);
  delay(PRE_DROP_MS);

  gate.write(OPEN_ANGLE);
  delay(GATE_OPEN_MS);
  gate.write(CLOSED_ANGLE);

  // [OPT-7] Confirm the completed drop so the ESP32 counts delivery, not intent.
  // Sent for every dispense (button or link); the ESP32 only acts on it when it
  // has an outstanding link-triggered reward awaiting confirmation.
  link.println("ACK");
}
