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

   Uses gpiozero software PWM on GPIO18, so it only runs with the pwm
   overlay *removed* from config.txt - with it, the kernel owns GPIO18. Stop
   the backend first: lgpio claims pins per process ("GPIO busy").

   With --sweep it instead drives the backend's own motor_control (20kHz
   hardware PWM) through a range of speeds and prints the wheel's measured
   deg/s at each - how to pick MOTOR_SPIN_SPEED_PERCENT.

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
    """Record motor commands instead of driving GPIO."""
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
    monkeypatch.setattr(volume, "_current_volume", 50.0)
    monkeypatch.setattr(volume, "_last_wheel_change_time", None)
    monkeypatch.setattr(volume, "_spin_commanded_at", None)
    monkeypatch.setattr(volume, "_stall_retry_at", None)
    monkeypatch.setattr(volume, "_rest_position", None)
    monkeypatch.setattr(volume, "_stillness_since", 0.0)
    monkeypatch.setattr(volume, "_was_halted", True)
    monkeypatch.setattr(volume, "_pending_push", None)
    monkeypatch.setattr(volume, "_encoder_sign_for_cw", 1)
    monkeypatch.setattr(volume, "_sign_candidate", 0)
    monkeypatch.setattr(volume, "_sign_streak", 0)
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


def test_kernel_pwm_sets_period_and_duty(tmp_path):
    channel = tmp_path / "pwm0"
    channel.mkdir()
    for name in ("duty_cycle", "period", "enable"):
        (channel / name).write_text("0")

    pwm = motor_control._KernelPWM(tmp_path, 0, 20000)
    assert (channel / "period").read_text() == "50000"  # 1e9 / 20kHz
    assert (channel / "enable").read_text() == "1"

    pwm.value = 0.3
    assert (channel / "duty_cycle").read_text() == "15000"
    assert pwm.value == 0.3


def test_spin_from_rest_kicks_twenty_percent_over(monkeypatch):
    monkeypatch.setattr(motor_control, "_init_hardware", lambda: None)
    monkeypatch.setattr(motor_control, "_current_speed_percent", 0.0)
    monkeypatch.setattr(motor_control, "KICK_DURATION_S", 0.05)

    motor_control.spin(30)
    assert motor_control._current_speed_percent == pytest.approx(36)
    time.sleep(0.15)
    assert motor_control._current_speed_percent == 30

    motor_control.spin(40)  # already moving: no kick
    assert motor_control._current_speed_percent == 40


def test_click_opposes_encoder_motion_once_sign_is_learned(wheel, monkeypatch):
    steps = []
    monkeypatch.setattr(motor_control, "step", lambda direction, *a, **kw: steps.append(direction))

    # Motor drives CW; this encoder counts *down* while it does.
    volume.set_playing(True)
    t0 = volume._spin_commanded_at
    for i in range(5):
        volume._tick(-22.5 * i, t0 + 0.2 * i)
    assert volume._encoder_sign_for_cw == -1

    # The user turns the wheel so the encoder counts up - in motor terms
    # that's CCW, so the click must push CW.
    with volume._lock:
        volume._apply_delta_locked(11.25)
    assert steps and set(steps) == {motor_control.DIRECTION_CCW}  # movement passed to step()


def test_brief_drag_against_motor_does_not_flip_sign(wheel):
    volume.set_playing(True)
    t0 = volume._spin_commanded_at
    positions = [0, 22.5, 45, 22.5, 45, 67.5]  # one backwards excursion
    for i, p in enumerate(positions):
        volume._tick(p, t0 + 0.2 * i)
    assert volume._encoder_sign_for_cw == 1


def test_stalled_motor_stops_and_retries(wheel, motor_calls):
    volume.set_playing(True)
    t0 = volume._spin_commanded_at
    volume._tick(0.0, t0)
    volume._tick(0.0, t0 + volume.STALL_SEC)
    assert motor_calls == ["spin", "stop"]
    volume._tick(0.0, t0 + volume.STALL_SEC + volume.STALL_RETRY_SEC)
    assert motor_calls == ["spin", "stop", "spin"]


def test_turning_wheel_is_not_a_stall(wheel, motor_calls):
    volume.set_playing(True)
    t0 = volume._spin_commanded_at
    for i in range(10):
        volume._tick(i * 22.5, t0 + i * 0.4)  # two encoder steps per tick
    assert motor_calls == ["spin"]


def test_pause_mid_turn_does_not_rearm(wheel, motor_calls):
    volume.set_playing(True)
    t0 = volume._spin_commanded_at
    volume._tick(0.0, t0)
    volume._tick(22.5, t0 + 0.1)
    volume._tick(22.5, t0 + 0.7)  # halted: the user grabbed it
    assert motor_calls == ["spin", "stop"]

    volume._tick(45.0, t0 + 0.8)  # turning the knob
    volume._tick(45.0, t0 + 1.4)  # pausing mid-turn
    assert motor_calls.count("stop") == 1
    assert volume._pending_push == volume.get_current_volume()


def test_device_volume_syncs_when_wheel_idle(wheel):
    volume.set_current_volume(80)
    assert volume.get_current_volume() == 80


def test_device_volume_ignored_while_knob_held(wheel, monkeypatch):
    monkeypatch.setattr(volume, "_armed", True)
    volume.set_current_volume(100)
    assert volume.get_current_volume() == 50


def test_device_volume_ignored_right_after_wheel_change(wheel, monkeypatch):
    # Spotify reports the pre-change level for a while; that mustn't snap
    # the knob back once it disarms.
    with volume._lock:
        volume._apply_delta_locked(9.0)  # +10 units
    volume.set_current_volume(100)
    assert volume.get_current_volume() == 60

    monkeypatch.setattr(volume, "_last_wheel_change_time",
                        volume._last_wheel_change_time - volume.DEVICE_SYNC_HOLD_SEC)
    volume.set_current_volume(100)
    assert volume.get_current_volume() == 100


def test_no_volume_command_to_device_without_volume_control(wheel, monkeypatch):
    from backend import spotify_api
    monkeypatch.setattr(spotify_api, "_device_id", None)
    monkeypatch.setattr(spotify_api, "_device_supports_volume", True)
    monkeypatch.setattr(spotify_api, "get_spotify_client",
                        lambda: pytest.fail("shouldn't talk to Spotify"))
    spotify_api._sync_volume_from_device(
        {"device": {"id": "phone", "name": "iPhone", "supports_volume": False, "volume_percent": 100}}
    )
    spotify_api.set_device_volume(40)


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


SWEEP_SPEEDS = (15, 20, 25, 30, 40, 50, 70, 100)


def sweep():
    """Find the slowest speed that actually turns the wheel, through the
    backend's own path - motor_control's 20kHz hardware PWM, measured by the
    encoder. main() drives gpiozero at 1kHz instead, so it passing doesn't
    prove the backend's spin() can start the wheel."""
    motor_control._init_hardware()
    if not motor_control._hardware_available or not encoder.is_available():
        print("needs the motor driver and encoder GPIO (see the log for why it failed)")
        return

    try:
        for speed in SWEEP_SPEEDS:
            motor_control.spin(speed)
            time.sleep(0.5)  # past spin-up
            start = encoder.get_position()
            time.sleep(2)
            print(f"  {speed:3d}%  {(encoder.get_position() - start) / 2:7.1f} deg/s")
            motor_control.spin(0)  # coast, not brake - let it settle
            time.sleep(1)
    finally:
        motor_control.spin(0)


if __name__ == "__main__":
    sweep() if "--sweep" in sys.argv else main()
