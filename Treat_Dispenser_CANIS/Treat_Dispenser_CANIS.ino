/*
  CANIS — Treat Dispenser (Arduino Uno)
  -------------------------------------
  Bench-tested and working. Buzzer marker tone -> short delay -> servo gate
  opens briefly -> closes. A narrowed "throat" above the gate keeps only one
  treat at the gate at a time; GATE_OPEN_MS is the main reliability knob.

  TWO ways to trigger a dispense now:
    1. The push-button (pin 2) — for standalone bench testing.
    2. A "DISPENSE" command from the ESP32 over a SoftwareSerial link.
       This is how the state machine fires a real reward.

  ESP32 link wiring (one-way, ESP32 -> Arduino):
    ESP32 GPIO 13 (TX2) ---> Arduino pin 10 (SoftwareSerial RX)
    ESP32 GND           ---> Arduino GND   (shared ground is required)
  We use SoftwareSerial (10/11) so the USB hardware Serial stays free for the
  Serial Monitor. 3.3V from the ESP32 into the Uno's 5V RX reads fine — no
  level shifter needed in this direction.
*/

#include <Servo.h>
#include <SoftwareSerial.h>

// --- Pins ---
const int SERVO_PIN  = 9;
const int BUZZER_PIN = 6;
const int BUTTON_PIN = 2;
const int LINK_RX    = 10;   // <- ESP32 GPIO 13
const int LINK_TX    = 11;   // unused (we only receive), leave unwired

// --- Servo angles (EYEBALL these against your bottle) ---
const int CLOSED_ANGLE = 80;   // flap fully covering the hole
const int OPEN_ANGLE   = 0;    // flap swung clear of the hole

// --- Timing (tune after the 20-trigger test) ---
const int MARKER_FREQ  = 2000; // marker tone, Hz
const int MARKER_MS    = 120;  // how long it beeps
const int PRE_DROP_MS  = 150;  // gap between beep and gate opening
const int GATE_OPEN_MS = 150;  // how long the flap stays open -- MAIN tuning knob

Servo gate;
SoftwareSerial link(LINK_RX, LINK_TX);   // RX, TX

int  lastButtonState = HIGH;   // INPUT_PULLUP idle = HIGH
int  dispenseCount   = 0;
char cmdBuf[16];
int  cmdLen = 0;

void setup() {
  Serial.begin(9600);
  link.begin(9600);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  gate.attach(SERVO_PIN);
  gate.write(CLOSED_ANGLE);     // start closed
  Serial.println("Dispenser ready. Press button or send DISPENSE over the link.");
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
      if (strcmp(cmdBuf, "DISPENSE") == 0) dispense("link");
      cmdLen = 0;
    } else if (cmdLen < (int)sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }
}

void dispense(const char* source) {
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
