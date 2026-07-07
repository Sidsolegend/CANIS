"""
test_calibration.py — pure-logic tests for the [C7] commissioning calibration.

Covers, with no hardware, the two "physically redo the assembly" fixes that the
guided self-test now turns into persisted software fixes:
  * motor invert flag — XORed into the direction sign (mirror of setMotor()).
  * servo flap-zero trim — clamped nudges + range-limited applied angle.

These mirror Treat_Dispenser_CANIS.ino (the Arduino Uno side); the firmware itself
persists them in EEPROM, but the decision logic is hardware-free and tested here.

Run:  pytest optimized/tests/test_calibration.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import canis_logic as L  # noqa: E402


# --------------------------------------------------------------------------- #
# Motor invert (mirror of the [C7] XOR in setMotor)
# --------------------------------------------------------------------------- #
def test_invert_flips_direction():
    # A forward speed with the invert flag set must energize the reversed IN-pair,
    # i.e. exactly what physically swapping the two motor leads used to do.
    assert L.motor_direction(200, invert=False) == ("HIGH", "LOW")
    assert L.motor_direction(200, invert=True) == ("LOW", "HIGH")


def test_invert_is_its_own_inverse():
    # Flipping the flag twice is a no-op: XOR twice returns the original sign.
    for speed in (-255, -1, 1, 200, 255):
        assert L.apply_motor_invert(L.apply_motor_invert(speed, True), True) == speed


def test_zero_speed_brakes_regardless_of_invert():
    # Stopped is stopped: both inputs LOW whether or not the motor is inverted.
    assert L.motor_direction(0, invert=False) == ("LOW", "LOW")
    assert L.motor_direction(0, invert=True) == ("LOW", "LOW")


def test_invert_reverses_a_backward_command_too():
    assert L.motor_direction(-200, invert=False) == ("LOW", "HIGH")
    assert L.motor_direction(-200, invert=True) == ("HIGH", "LOW")


# --------------------------------------------------------------------------- #
# Servo trim clamp (mirror of clampTrim / nudgeTrim)
# --------------------------------------------------------------------------- #
def test_clamp_trim_saturates_both_ends():
    assert L.clamp_trim(100) == L.TRIM_MAX_DEG
    assert L.clamp_trim(-100) == -L.TRIM_MAX_DEG
    assert L.clamp_trim(0) == 0
    assert L.clamp_trim(L.TRIM_MAX_DEG) == L.TRIM_MAX_DEG


def test_nudge_trim_saturates_after_repeated_steps():
    # Spamming TRIM+ must plateau at the max, never run away past it.
    t = 0
    for _ in range(20):
        t = L.nudge_trim(t, L.TRIM_STEP_DEG)
    assert t == L.TRIM_MAX_DEG
    for _ in range(20):
        t = L.nudge_trim(t, -L.TRIM_STEP_DEG)
    assert t == -L.TRIM_MAX_DEG


def test_step_is_within_range():
    # A single step must never overshoot the whole legal range in one press.
    assert L.TRIM_STEP_DEG <= L.TRIM_MAX_DEG


# --------------------------------------------------------------------------- #
# Applied servo angle (mirror of closedAngle / openAngle constrain)
# --------------------------------------------------------------------------- #
def test_trimmed_angle_applies_offset():
    assert L.trimmed_angle(80, 0) == 80
    assert L.trimmed_angle(80, 5) == 85
    assert L.trimmed_angle(80, -5) == 75


def test_trimmed_angle_clamps_out_of_range_offset_before_applying():
    # A stored offset beyond the legal trim is clamped first, so the flap can only
    # move by at most TRIM_MAX_DEG from the nominal angle.
    assert L.trimmed_angle(80, 100) == 80 + L.TRIM_MAX_DEG
    assert L.trimmed_angle(80, -100) == 80 - L.TRIM_MAX_DEG


def test_trimmed_angle_never_leaves_servo_range():
    # OPEN_ANGLE is 0; a negative trim must not command a negative angle (would be
    # an illegal SG90 position). Constrain to [0,180] holds at both ends.
    assert L.trimmed_angle(0, -15) == 0
    assert L.trimmed_angle(180, 15) == 180
    for base in (0, 80, 180):
        for off in range(-L.TRIM_MAX_DEG, L.TRIM_MAX_DEG + 1):
            a = L.trimmed_angle(base, off)
            assert L.SERVO_MIN_DEG <= a <= L.SERVO_MAX_DEG
