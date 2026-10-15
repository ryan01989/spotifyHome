import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# TB6612 driver, channel A (motor on AO1/AO2). BCM numbering. These match the
# wiring documented and bench-validated in motor/motor_test.py - keep the two
# in step.
GPIO_STBY = int(os.getenv("MOTOR_GPIO_STBY", 4))
GPIO_PWMA = int(os.getenv("MOTOR_GPIO_PWMA", 18))
GPIO_AIN1 = int(os.getenv("MOTOR_GPIO_AIN1", 17))
GPIO_AIN2 = int(os.getenv("MOTOR_GPIO_AIN2", 27))

DIRECTION_CW = 1
DIRECTION_CCW = -1

# Under load the N20 stalls below some duty that has to be measured on the
# real wheel (motor_test.py --sweep); volume.py logs a stall when it happens.
# Env-tunable so the Pi can be adjusted without a code change.
DEFAULT_SPIN_SPEED_PERCENT = float(os.getenv("MOTOR_SPIN_SPEED_PERCENT", 50))

# Driven exactly the way the bench-proven tb6612_gui_openloop.py does it:
# gpiozero on lgpio (python3-lgpio from apt - the venv is built with
# --system-site-packages to see it), lgpio software PWM at 1kHz. The earlier
# design - pigpio's hardware_PWM() at 20kHz on the PWM peripheral - hasn't
# driven the motor since the move to Trixie and channel A. 1kHz is audible;
# lgpio offers up to ~8kHz.
PWM_FREQUENCY_HZ = int(os.getenv("MOTOR_PWM_FREQUENCY_HZ", 8000))

# Kick-start, as in the bench test: a brushed motor's starting torque depends
# on where the rotor happens to sit, so every start from rest below
# KICK_THRESHOLD_PERCENT fires KICK_SPEED_PERCENT for KICK_DURATION_S before
# settling to the commanded speed. Above the threshold the command is its
# own kick.
KICK_SPEED_PERCENT = 100
KICK_DURATION_S = 0.12
KICK_THRESHOLD_PERCENT = 85

# stop(): ramp to 0 over this long rather than cutting power immediately.
CONTROLLED_STOP_DURATION_S = 1.0
CONTROLLED_STOP_STEP_S = 0.02

# step(): brief opposing pulse volume.py uses for a physical "click". 40%
# barely cleared the drivetrain's friction and felt like slop rather than a
# detent - a click wants full force, kept short.
STEP_SPEED_PERCENT = float(os.getenv("MOTOR_STEP_SPEED_PERCENT", 80))
STEP_DURATION_S = float(os.getenv("MOTOR_STEP_DURATION_S", 0.04))

# This module doesn't report whether the wheel is moving under its own power.
# volume.py tells motor motion from user motion by when it's armed (between a
# halt and RESTART_IDLE_SEC of stillness), not by asking the motor. See the
# volume-knob TODOs in CLAUDE.md for what that doesn't cover.

_lock = threading.Lock()
_init_lock = threading.Lock()
_stby = _in1 = _in2 = _pwm = None  # gpiozero devices, once initialized
_hardware_available = False
_hardware_attempted = False

_current_speed_percent = 0.0
_current_direction = DIRECTION_CW
_generation = 0  # bumped on every new command; lets stale ramp/pulse threads bail out


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
            from gpiozero import DigitalOutputDevice, PWMOutputDevice
        except ImportError:
            logger.warning("gpiozero not available; motor control disabled")
            return

        try:
            in1 = DigitalOutputDevice(GPIO_AIN1, initial_value=False)
            in2 = DigitalOutputDevice(GPIO_AIN2, initial_value=False)
            pwm = PWMOutputDevice(
                GPIO_PWMA, frequency=PWM_FREQUENCY_HZ, initial_value=0)
            stby = DigitalOutputDevice(GPIO_STBY, initial_value=False)
            stby.on()  # take the driver out of standby
        except Exception as e:
            logger.warning(
                f"Motor GPIO init failed; motor control disabled: {e}")
            return

        _stby, _in1, _in2, _pwm = stby, in1, in2, pwm
        _hardware_available = True
        # The pin factory is the thing to check if the motor goes quiet again:
        # LGPIOFactory is the proven path; PiGPIOFactory means lgpio wasn't
        # importable and gpiozero fell back.
        logger.info(
            f"Motor control ready on {type(pwm.pin_factory).__name__}, PWM {PWM_FREQUENCY_HZ}Hz"
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

    Starting from rest below KICK_THRESHOLD_PERCENT, kicks at
    KICK_SPEED_PERCENT first and then settles to the requested speed, so the
    start doesn't depend on where the rotor happened to stop.
    """
    _init_hardware()
    if speed_percent is None:
        speed_percent = DEFAULT_SPIN_SPEED_PERCENT
    speed_percent = max(0.0, min(100.0, speed_percent))

    global _generation
    with _lock:
        needs_kick = 0 < speed_percent < KICK_THRESHOLD_PERCENT and _current_speed_percent == 0
        _generation += 1
        my_generation = _generation
        _drive_locked(
            KICK_SPEED_PERCENT if needs_kick else speed_percent, direction)

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
