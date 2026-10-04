/*
  CANIS — encoder test (a tiny stand-alone sketch, for bring-up only)
  ====================================================================
  Counts the two wheel encoders and prints the numbers, WITHOUT switching on mobile mode, the Pi controller,
  or the motors. Flash it to the ROBOT's ESP32 (the CP2102 board), turn the wheels by hand, watch the numbers.
  When you are done, flash CANIS_Core.ino back onto the same board.

  Uses the same pins and the same hardware counting method as CANIS_Core.ino (canis_config.h is the single
  source for the pins):   left A/B = CANIS_ENC_L_A_PIN / _B_PIN,   right A/B = CANIS_ENC_R_A_PIN / _B_PIN.

  Serial Monitor, 115200 baud. Keys:  z = set both counts to zero     (anything else is ignored)

  What you should see (wheel turned by hand, looking at the wheel from its OUTSIDE, forward = the dispenser end):
    * turn the LEFT wheel forward   -> L climbs;   backward -> L falls;   R does not move
    * turn the RIGHT wheel forward  -> R climbs;   backward -> R falls;   L does not move
  If a count goes the wrong way, swap THAT encoder's two signal wires (C1 and C2). Do not swap the motor wires.
  The "levels" show the raw A/B pins (0 or 1): they must change as you turn the wheel, even before counting works.

  Counts per wheel turn: press z, turn ONE wheel exactly 10 full turns, then read the number: the "/10" value
  is counts per turn, the value to put in CANIS_ENC_COUNTS_PER_REV.   (Stay under ~30000 counts: 10 turns is fine.)
*/

#include <Arduino.h>
#include "driver/pcnt.h"
#include "../../canis_config.h"

static const int PINS[2][2] = {
  { CANIS_ENC_L_A_PIN, CANIS_ENC_L_B_PIN },
  { CANIS_ENC_R_A_PIN, CANIS_ENC_R_B_PIN },
};
static const char* NAMES[2] = { "LEFT ", "RIGHT" };

static void setupEncoder(int unitNum, int pinA, int pinB) {
  pcnt_unit_t unit = (pcnt_unit_t)unitNum;
  pinMode(pinA, INPUT_PULLUP);    // no effect on GPIO 36/39 (input-only, no pull-ups): the encoder drives them itself
  pinMode(pinB, INPUT_PULLUP);

  pcnt_config_t cfg = {};
  cfg.unit = unit;
  cfg.counter_h_lim = 32767;
  cfg.counter_l_lim = -32768;

  cfg.channel = PCNT_CHANNEL_0;               // A edges, B = direction
  cfg.pulse_gpio_num = pinA;
  cfg.ctrl_gpio_num = pinB;
  cfg.pos_mode = PCNT_COUNT_INC;
  cfg.neg_mode = PCNT_COUNT_DEC;
  cfg.lctrl_mode = PCNT_MODE_KEEP;
  cfg.hctrl_mode = PCNT_MODE_REVERSE;
  pcnt_unit_config(&cfg);

  cfg.channel = PCNT_CHANNEL_1;               // B edges, A = direction (opposite sense)
  cfg.pulse_gpio_num = pinB;
  cfg.ctrl_gpio_num = pinA;
  cfg.pos_mode = PCNT_COUNT_DEC;
  cfg.neg_mode = PCNT_COUNT_INC;
  cfg.lctrl_mode = PCNT_MODE_KEEP;
  cfg.hctrl_mode = PCNT_MODE_REVERSE;
  pcnt_unit_config(&cfg);

  pcnt_set_filter_value(unit, 1023);          // ~12.8 us glitch filter, same as the real firmware
  pcnt_filter_enable(unit);
  pcnt_counter_pause(unit);
  pcnt_counter_clear(unit);
  pcnt_counter_resume(unit);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nCANIS encoder test. Turn a wheel by hand. 'z' zeroes the counts.");
  for (int i = 0; i < 2; i++) {
    setupEncoder(i, PINS[i][0], PINS[i][1]);
    Serial.printf("  %s encoder: A = GPIO %d, B = GPIO %d\n", NAMES[i], PINS[i][0], PINS[i][1]);
  }
}

void loop() {
  while (Serial.available()) {
    if (Serial.read() == 'z') {
      for (int i = 0; i < 2; i++) pcnt_counter_clear((pcnt_unit_t)i);
      Serial.println("  [counts set to zero]");
    }
  }
  static unsigned long last = 0;
  if (millis() - last < 250) return;
  last = millis();

  int16_t c[2];
  for (int i = 0; i < 2; i++) pcnt_get_counter_value((pcnt_unit_t)i, &c[i]);
  Serial.printf("L=%6d (/10 = %7.1f)  levels A%d B%d   |   R=%6d (/10 = %7.1f)  levels A%d B%d\n",
                c[0], c[0] / 10.0f, digitalRead(PINS[0][0]), digitalRead(PINS[0][1]),
                c[1], c[1] / 10.0f, digitalRead(PINS[1][0]), digitalRead(PINS[1][1]));
}
