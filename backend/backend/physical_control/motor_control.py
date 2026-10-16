import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# TB6612 driver, channel A (motor on AO1/AO2). BCM numbering. These match the
# wiring documented and bench-validated in motor/motor_test.py - keep the two
# in step.
GPIO_STBY = int(os.getenv("MOTOR_GPIO_STBY", 4))
GPIO_AIN1 = int(os.getenv("MOTOR_GPIO_AIN1", 17))
GPIO_AIN2 = int(os.getenv("MOTOR_GPIO_AIN2", 27))
# PWMA is wired to GPIO18, but nothing here claims that pin: the kernel's PWM
# driver owns it via `dtoverlay=pwm,pin=18,func=2` in /boot/firmware/config.txt.
GPIO_PWMA = 18

DIRECTION_CW = -1
DIRECTION_CCW = 1

# 30% at 20kHz turns the wheel ~16 rev/min, smooth and quiet on the real
# build - comfortably above the ~22deg/s volume.py needs to not read the
# spin as a grab.
DEFAULT_SPIN_SPEED_PERCENT = float(os.getenv("MOTOR_SPIN_SPEED_PERCENT", 30))

# Speed is kernel hardware PWM through sysfs - the BCM PWM peripheral, set up
# by the overlay above. lgpio's software PWM tops out around 10kHz, which is
# loud; 20kHz is above hearing and was bench-tested by hand through these same
# sysfs files. Direction and standby stay on gpiozero/lgpio. (pigpio's
# hardware_PWM() on the same peripheral never drove the motor on Trixie.)
PWM_FREQUENCY_HZ = int(os.getenv("MOTOR_PWM_FREQUENCY_HZ", 20000))
PWM_CHIP = Path(os.getenv("MOTOR_PWM_CHIP", "/sys/class/pwm/pwmchip0"))
PWM_CHANNEL = int(os.getenv("MOTOR_PWM_CHANNEL", 0)
                  )  # GPIO18 is PWM0 channel 0

# After exporting a channel, udev chgrps its files to `gpio` a moment later;
# writes before that fail with PermissionError for a non-root user.
_PWM_EXPORT_SETTLE_S = 2.0

# Kick-start: a brushed motor's starting torque depends on where the rotor
# happens to sit, so every start from rest runs at KICK_FACTOR x the
# commanded speed (capped at 100%) for KICK_DURATION_S before settling. Kept
# gentle on purpose rather than the bench GUI's full-power kick.
KICK_FACTOR = float(os.getenv("MOTOR_KICK_FACTOR", 1.2))
KICK_DURATION_S = 0.12

# stop(): ramp to 0 over this long rather than cutting power immediately.
CONTROLLED_STOP_DURATION_S = 1.0
CONTROLLED_STOP_STEP_S = 0.02

# step(): brief opposing pulse volume.py uses for a physical "click". 40%
# barely cleared the drivetrain's friction and felt like slop rather than a
# detent - a click wants full force, kept short.
STEP_SPEED_PERCENT = float(os.getenv("MOTOR_STEP_SPEED_PERCENT", 30))
STEP_DURATION_S = float(os.getenv("MOTOR_STEP_DURATION_S", 0.04))

# This module doesn't report whether the wheel is moving under its own power.
# volume.py tells motor motion from user motion by when it's armed (between a
# halt and RESTART_IDLE_SEC of stillness), not by asking the motor. See the
# volume-knob TODOs in CLAUDE.md for what that doesn't cover.

_lock = threading.Lock()
_init_lock = threading.Lock()
_stby = _in1 = _in2 = None  # gpiozero devices, once initialized
_pwm = None  # _KernelPWM, once initialized
_hardware_available = False
_hardware_attempted = False

_current_speed_percent = 0.0
_current_direction = DIRECTION_CW
_generation = 0  # bumped on every new command; lets stale ramp/pulse threads bail out


class _KernelPWM:
    """One sysfs PWM channel, with a gpiozero-style 0.0-1.0 `value`."""

    def __init__(self, chip, channel, frequency_hz):
        self._dir = chip / f"pwm{channel}"
        if not self._dir.exists():
            (chip / "export").write_text(str(channel))
        self._period_ns = round(1_000_000_000 / frequency_hz)

        deadline = time.monotonic() + _PWM_EXPORT_SETTLE_S
        while True:
            try:
                # Duty first: the kernel rejects a duty_cycle longer than the
                # period, and a previous run may have left a longer one.
                self._write("duty_cycle", 0)
                break
            except PermissionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        self._write("period", self._period_ns)
        self._write("enable", 1)

    def _write(self, name, value):
        (self._dir / name).write_text(str(value))

    @property
    def value(self):
        return int((self._dir / "duty_cycle").read_text()) / self._period_ns

    @value.setter
    def value(self, fraction):
        self._write("duty_cycle", round(
            self._period_ns * max(0.0, min(1.0, fraction))))


def _init_hardware():
    # Lazy so importing this module (e.g. via routes.py) never crashes on a
    # machine with no GPIO, such as the Mac used for local dev - it just
    # leaves the motor commands as no-ops.
    global _stby, _in1, _in2, _pwm, _hardware_available, _hardware_attempted
    if _hardware_attempted:
        return

    with _init_lock:
        if _hardware_attempted:
            return
        _hardware_attempted = True

        try:
            from gpiozero import DigitalOutputDevice
        except ImportError:
            logger.warning("gpiozero not available; motor control disabled")
            return

        try:
            in1 = DigitalOutputDevice(GPIO_AIN1, initial_value=False)
            in2 = DigitalOutputDevice(GPIO_AIN2, initial_value=False)
            stby = DigitalOutputDevice(GPIO_STBY, initial_value=False)
        except Exception as e:
            logger.warning(
                f"Motor GPIO init failed; motor control disabled: {e}")
            return

        try:
            pwm = _KernelPWM(PWM_CHIP, PWM_CHANNEL, PWM_FREQUENCY_HZ)
        except OSError as e:
            for device in (in1, in2, stby):
                device.close()
            hint = (
                "is `dtoverlay=pwm,pin=18,func=2` in /boot/firmware/config.txt (then reboot)?"
                if not PWM_CHIP.exists()
                else "is this user in the `gpio` group?"
            )
            logger.warning(
                f"Motor PWM init failed ({e}); motor control disabled - {hint}")
            return

        stby.on()  # take the driver out of standby
        _stby, _in1, _in2, _pwm = stby, in1, in2, pwm
        _hardware_available = True
        # LGPIOFactory is the proven path for the direction pins;
        # PiGPIOFactory means lgpio wasn't importable and gpiozero fell back.
        logger.info(
            f"Motor control ready: pins on {type(stby.pin_factory).__name__}, "
            f"kernel PWM {PWM_FREQUENCY_HZ}Hz on {PWM_CHIP.name}/pwm{PWM_CHANNEL}"
        )


def _drive_locked(speed_percent, direction):
    """Set direction pins + PWM duty for continuous drive. Caller holds _lock."""
    global _current_speed_percent, _current_direction
    speed_percent = max(0.0, min(100.0, speed_percent))

    if _hardware_available:
        if speed_percent == 0:
            _in1.off()
            _in2.off()
            _pwm.value = 0
        else:
            _in1.value = direction == DIRECTION_CW
            _in2.value = direction != DIRECTION_CW
            _pwm.value = speed_percent / 100.0

    _current_speed_percent = speed_percent
    _current_direction = direction


def _brake_locked():
    """Short brake per the TB6612 truth table (AIN1=AIN2=H). Caller holds _lock."""
    global _current_speed_percent
    if _hardware_available:
        _in1.on()
        _in2.on()
        _pwm.value = 1.0
    _current_speed_percent = 0.0


def spin(speed_percent=None, direction=DIRECTION_CW):
    """Drive the motor continuously at speed_percent (0-100) in direction.

    Starting from rest, kicks at KICK_FACTOR x speed_percent first and then
    settles to the requested speed, so the start doesn't depend on where the
    rotor happened to stop.
    """
    _init_hardware()
    if speed_percent is None:
        speed_percent = DEFAULT_SPIN_SPEED_PERCENT
    speed_percent = max(0.0, min(100.0, speed_percent))
    kick_percent = min(100.0, speed_percent * KICK_FACTOR)

    global _generation
    with _lock:
        needs_kick = 0 < speed_percent < kick_percent and _current_speed_percent == 0
        _generation += 1
        my_generation = _generation
        _drive_locked(kick_percent if needs_kick else speed_percent, direction)

    if needs_kick:
        def _settle():
            time.sleep(KICK_DURATION_S)
            with _lock:
                if my_generation != _generation:
                    return  # superseded by a newer command
                _drive_locked(speed_percent, direction)

        threading.Thread(target=_settle, daemon=True).start()


def stop():
    """Ramp the motor down to 0 over CONTROLLED_STOP_DURATION_S, rather than
    cutting power immediately."""
    _init_hardware()

    global _generation
    with _lock:
        _generation += 1
        my_generation = _generation
        start_speed = _current_speed_percent
        direction = _current_direction

    if start_speed <= 0:
        return

    def _ramp():
        steps = max(1, int(CONTROLLED_STOP_DURATION_S / CONTROLLED_STOP_STEP_S))
        for i in range(1, steps + 1):
            with _lock:
                if my_generation != _generation:
                    return  # superseded by a newer command
                _drive_locked(start_speed * (1 - i / steps), direction)
            time.sleep(CONTROLLED_STOP_STEP_S)
        with _lock:
            if my_generation == _generation:
                _drive_locked(0, direction)

    threading.Thread(target=_ramp, daemon=True).start()


def stop_immediately():
    """Fast stop via TB6612 short brake (AIN1=AIN2=H) - quicker than stop(),
    but safer than just cutting PWM/STBY."""
    _init_hardware()

    global _generation
    with _lock:
        _generation += 1
        _brake_locked()


def step(movement_direction, speed_percent=STEP_SPEED_PERCENT, duration_s=STEP_DURATION_S):
    """Fire a brief pulse opposite movement_direction - the physical "click"
    volume.py uses to push back against the user's spin. Non-blocking,
    safe to call rapidly from the encoder callback."""
    _init_hardware()
    opposite = DIRECTION_CCW if movement_direction == DIRECTION_CW else DIRECTION_CW

    global _generation
    with _lock:
        _generation += 1
        my_generation = _generation
        _drive_locked(speed_percent, opposite)

    def _release():
        time.sleep(duration_s)
        with _lock:
            if my_generation != _generation:
                return  # superseded by a newer command
            _drive_locked(0, opposite)

    threading.Thread(target=_release, daemon=True).start()
