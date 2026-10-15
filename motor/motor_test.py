#!/usr/bin/env python3
"""
Motor and wheel tests, in two parts:

1. Unit tests (pytest) for the wheel logic - play/pause driving the motor,
   the volume knob's turn-taking, and startup calibration. No hardware;
   motor commands and the encoder are faked. Collected by `make test` from
   backend/ (pyproject's testpaths includes ../motor).

2. A hardware bench test for the N20 motor on the TB6612FNG, run directly
   on the Pi:

       cd backend && .venv/bin/python ../motor/motor_test.py

   Uses gpiozero; in the venv that drives the pins through pigpiod (no lgpio
   there), so `sudo pigpiod` first and stop the backend so they don't fight.

Wiring (BCM numbering) - read from motor_control.py so the bench and the
backend can't disagree:

    STBY -> GPIO4    (header pin 7)
    PWMA -> GPIO18   (header pin 12)
    AIN1 -> GPIO17   (header pin 11)
    AIN2 -> GPIO27   (header pin 13)

Motor is on AO1/AO2 (channel A). Channel B is unused.
"""

import json
import signal
import sys
import time

import pytest

from backend import create_app
from backend.physical_control import calibration, encoder, motor_control, volume


# ===================================================================== unit tests

@pytest.fixture
def motor_calls(monkeypatch):
    """Record motor commands instead of driving pigpio."""
    calls = []
    for name in ("spin", "stop", "stop_immediately", "step"):
        monkeypatch.setattr(motor_control, name, lambda *a, _n=name, **kw: calls.append(_n))
    return calls


@pytest.fixture
def wheel(monkeypatch, motor_calls):
    """Fresh volume/calibration state, no monitor thread, fake encoder."""
    monkeypatch.setattr(volume, "_init", lambda: None)
    monkeypatch.setattr(volume, "_motor_wanted", False)
    monkeypatch.setattr(volume, "_suspended", False)
    monkeypatch.setattr(volume, "_armed", False)
    monkeypatch.setattr(calibration, "_saved_steps_per_rev", None)
    monkeypatch.setattr(calibration, "_prompt_pending", False)
    monkeypatch.setattr(calibration, "_start_steps", None)

    steps = {"count": 0}
    monkeypatch.setattr(encoder, "is_available", lambda: True)
    monkeypatch.setattr(encoder, "get_steps", lambda: steps["count"])
    monkeypatch.setattr(encoder, "STEPS_PER_REV", encoder.STEPS_PER_REV)
    monkeypatch.setattr(encoder, "DEGREES_PER_STEP", encoder.DEGREES_PER_STEP)
    return steps


@pytest.fixture
def calibration_file(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    monkeypatch.setenv("WHEEL_CALIBRATION_PATH", str(path))
    monkeypatch.setattr(calibration, "PROMPT_TIMEOUT_SEC", 3600)
    return path


def test_default_is_32_steps_per_rev():
    # 16 magnets mounted N,S,N,S = 16 poles, 2 quadrature steps per pole.
    assert calibration.EXPECTED_STEPS_PER_REV == 32


def test_motor_follows_play_pause(wheel, motor_calls):
    volume.set_playing(True)
    volume.set_playing(True)  # no change, no command
    volume.set_playing(False)
    assert motor_calls == ["spin", "stop"]


def test_play_while_knob_held_waits_for_release(wheel, motor_calls, monkeypatch):
    monkeypatch.setattr(volume, "_armed", True)
    volume.set_playing(True)
    assert motor_calls == []


def test_suspend_holds_motor_until_resume(wheel, motor_calls):
    volume.suspend()
    volume.set_playing(True)
    assert motor_calls == []
    volume.resume()
    assert motor_calls == ["spin"]


def test_load_prompts_and_holds_the_wheel(wheel, calibration_file):
    calibration.load()
    status = calibration.status()
    assert status["prompt"] and not status["calibrated"]
    assert volume._suspended


def test_load_applies_saved_calibration(wheel, calibration_file):
    calibration_file.write_text(json.dumps({"steps_per_rev": 30}))
    calibration.load()
    assert encoder.STEPS_PER_REV == 30
    assert encoder.DEGREES_PER_STEP == 12.0


def test_turn_within_one_step_saves_expected(wheel, calibration_file, motor_calls):
    calibration.load()
    volume.set_playing(True)
    calibration.start()
    wheel["count"] = -31  # direction doesn't matter, one step short does
    calibration.finish()

    assert json.loads(calibration_file.read_text()) == {"steps_per_rev": 32}
    assert not calibration.status()["prompt"]
    assert motor_calls == ["spin"]  # the wheel goes back to the motor


def test_mismatch_asks_before_saving(wheel, calibration_file):
    calibration.load()
    calibration.start()
    wheel["count"] = 20
    with pytest.raises(calibration.CalibrationMismatch):
        calibration.finish()
    assert calibration.status()["in_progress"]

    calibration.finish(force=True)
    assert json.loads(calibration_file.read_text()) == {"steps_per_rev": 20}
    assert encoder.STEPS_PER_REV == 20


def test_too_few_steps_is_rejected(wheel, calibration_file):
    calibration.load()
    calibration.start()
    wheel["count"] = 2
    with pytest.raises(calibration.CalibrationError):
        calibration.finish(force=True)
    assert not calibration_file.exists()


def test_skip_keeps_scale_and_releases_wheel(wheel, calibration_file):
    calibration.load()
    calibration.skip()
    assert not calibration.status()["prompt"]
    assert not volume._suspended
    assert not calibration_file.exists()


def test_calibration_route_without_encoder(calibration_file, monkeypatch, motor_calls):
    # The dev Mac: no encoder, so the startup prompt dismisses itself.
    monkeypatch.setattr(encoder, "is_available", lambda: False)
    client = create_app({"TESTING": True}).test_client()
    response = client.get("/api/calibration")
    assert response.status_code == 200
    assert response.json["available"] is False
    assert response.json["prompt"] is False


# ================================================================= bench test

STBY_PIN = motor_control.GPIO_STBY
PWMA_PIN = motor_control.GPIO_PWMA
AIN1_PIN = motor_control.GPIO_AIN1
AIN2_PIN = motor_control.GPIO_AIN2

PWM_FREQ = 1000   # Hz. 1 kHz is above the audible whine of 100 Hz and well
# under the TB6612's 100 kHz ceiling.

MIN_DUTY = 0.35   # N20s typically won't break static friction below ~30-40%.


class TB6612Channel:
    """One half of a TB6612FNG: two direction pins plus a PWM speed pin."""

    def __init__(self, in1, in2, pwm, standby, freq=PWM_FREQ):
        # Imported here so pytest collecting this file never touches GPIO.
        from gpiozero import DigitalOutputDevice, PWMOutputDevice

        self.in1 = DigitalOutputDevice(in1, initial_value=False)
        self.in2 = DigitalOutputDevice(in2, initial_value=False)
        self.pwm = PWMOutputDevice(pwm, frequency=freq, initial_value=0)
        self.standby = DigitalOutputDevice(standby, initial_value=False)

    def enable(self):
        """Bring the driver out of standby. Must be high for any output."""
        self.standby.on()

    def forward(self, speed=1.0):
        """IN1=H, IN2=L -> CW."""
        self.in1.on()
        self.in2.off()
        self.pwm.value = _clamp(speed)

    def reverse(self, speed=1.0):
        """IN1=L, IN2=H -> CCW."""
        self.in1.off()
        self.in2.on()
        self.pwm.value = _clamp(speed)

    def brake(self):
        """IN1=H, IN2=H -> short brake. Both outputs pulled low, motor
        shorted through the bridge. Stops fast; also dumps the motor's
        kinetic energy into the driver, so don't hammer it."""
        self.in1.on()
        self.in2.on()
        self.pwm.value = 1.0

    def coast(self):
        """IN1=L, IN2=L -> outputs high-Z. Motor freewheels to a halt."""
        self.in1.off()
        self.in2.off()
        self.pwm.value = 0

    def ramp(self, target, seconds=1.0, steps=40, reverse=False):
        """Ease the duty cycle to `target` so we don't slam the supply."""
        start = self.pwm.value
        drive = self.reverse if reverse else self.forward
        for i in range(1, steps + 1):
            drive(start + (target - start) * i / steps)
            time.sleep(seconds / steps)

    def close(self):
        """Cut drive, drop into standby, release the pins."""
        self.coast()
        self.standby.off()
        for dev in (self.in1, self.in2, self.pwm, self.standby):
            dev.close()


def _clamp(v):
    return max(0.0, min(1.0, v))


def main():
    motor = TB6612Channel(AIN1_PIN, AIN2_PIN, PWMA_PIN, STBY_PIN)

    def shutdown(*_):
        print("\nstopping...")
        motor.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        print("enabling driver (STBY high)")
        motor.enable()
        time.sleep(0.5)

        print("ramping forward to 100%")
        motor.ramp(1.0, seconds=1.5)
        time.sleep(2)

        print("coasting")
        motor.coast()
        time.sleep(2)

        print("ramping reverse to 100%")
        motor.ramp(1.0, seconds=1.5, reverse=True)
        time.sleep(2)

        print("braking")
        motor.brake()
        time.sleep(1)

        print("stepping through speeds, forward")
        for duty in (MIN_DUTY, 0.5, 0.75, 1.0):
            print(f"  duty = {duty:.0%}")
            motor.forward(duty)
            time.sleep(1.5)

        print("done")

    finally:
        motor.close()


if __name__ == "__main__":
    main()
