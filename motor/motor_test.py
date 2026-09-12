#!/usr/bin/env python3
"""
Quick bench test for an N20 DC motor on a TB6612FNG, driven by a Pi Zero 2 W.

Wiring assumed (BCM numbering) -- edit the constants below to match your board:

    STBY -> GPIO16   
    PWMB -> GPIO18   
    BIN1 -> GPIO20   
    BIN2 -> GPIO21   

Motor is on BO1/BO2 (channel B). Channel A is unused.

Setup:
    sudo apt install python3-gpiozero python3-lgpio

Run:
    python3 tb6612_test.py
"""

import signal
import sys
import time

from gpiozero import DigitalOutputDevice, PWMOutputDevice

# ---------------------------------------------------------------- pin config
STBY_PIN = 16
PWMB_PIN = 18
BIN1_PIN = 20
BIN2_PIN = 21

PWM_FREQ = 1000   # Hz. 1 kHz is above the audible whine of 100 Hz and well
# under the TB6612's 100 kHz ceiling.

MIN_DUTY = 0.35   # N20s typically won't break static friction below ~30-40%.


class TB6612Channel:
    """One half of a TB6612FNG: two direction pins plus a PWM speed pin."""

    def __init__(self, in1, in2, pwm, standby, freq=PWM_FREQ):
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
    motor = TB6612Channel(BIN1_PIN, BIN2_PIN, PWMB_PIN, STBY_PIN)

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
