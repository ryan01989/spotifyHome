import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# TB6612 driver, channel B (channel A is dead - see motor-project-notes.md).
# BCM numbering. These match the wiring documented and bench-validated in
# motor/motor_test.py - keep the two in step.
GPIO_STBY = int(os.getenv("MOTOR_GPIO_STBY", 16))
GPIO_PWMB = int(os.getenv("MOTOR_GPIO_PWMB", 18))  # hardware PWM channel
GPIO_BIN1 = int(os.getenv("MOTOR_GPIO_BIN1", 20))
GPIO_BIN2 = int(os.getenv("MOTOR_GPIO_BIN2", 21))

DIRECTION_CW = 1
DIRECTION_CCW = -1

DEFAULT_SPIN_SPEED_PERCENT = 50

# True hardware PWM via pigpio.hardware_PWM(), not gpiozero/pigpio software
# PWM - software PWM gets ragged above ~8kHz on a Zero 2 W and pigpio's
# software table doesn't even offer 20kHz. 20kHz is above the audible range
# (no motor whine) and is what tb6612_hw20k_test.py validated as clean.
PWM_FREQUENCY_HZ = 20000

# Hop-start: rotor position at rest can stall a slow start. Below
# HOP_START_THRESHOLD_PERCENT, kick to HOP_START_SPEED_PERCENT (just enough
# to overcome stall torque, not the old 100%-duty kick) for
# HOP_START_DURATION_S before settling to the requested speed.
HOP_START_THRESHOLD_PERCENT = 30
HOP_START_SPEED_PERCENT = 35
HOP_START_DURATION_S = 0.12

# stop(): ramp to 0 over this long rather than cutting power immediately.
CONTROLLED_STOP_DURATION_S = 1.0
CONTROLLED_STOP_STEP_S = 0.02

# step(): brief opposing pulse used by haptic_feedback for a physical "click".
STEP_SPEED_PERCENT = 40
STEP_DURATION_S = 0.04

# How long after the motor stops the wheel may still be coasting from what
# the motor did. volume.py reads is_driving()/is_stepping() so motor-driven
# motion never gets mistaken for the user turning the wheel.
DRIVE_COAST_SETTLE_S = 0.25
# Must outlast DRIVE_COAST_SETTLE_S, so a step()'s own coast-down never
# starts reporting as continuous drive once the step window expires.
STEP_SETTLE_S = DRIVE_COAST_SETTLE_S + 0.1

_lock = threading.Lock()
_init_lock = threading.Lock()
_pi = None
_hardware_available = False
_hardware_attempted = False

_current_speed_percent = 0.0
_current_direction = DIRECTION_CW
_generation = 0  # bumped on every new command; lets stale ramp/pulse threads bail out
_drive_stopped_at = 0.0  # monotonic time the motor last went from moving to idle
_step_active_until = 0.0  # monotonic deadline for the current step() pulse + settle


def _init_hardware():
    # Lazy so importing this module (e.g. via routes.py) never crashes on a
    # machine with no GPIO/pigpiod, such as the Mac used for local dev - it
    # just leaves the motor commands as no-ops.
    global _pi, _hardware_available, _hardware_attempted
    if _hardware_attempted:
        return

    with _init_lock:
        if _hardware_attempted:
            return
        _hardware_attempted = True

        try:
            import pigpio
        except ImportError:
            logger.warning("pigpio not available; motor control disabled")
            return

        try:
            pi = pigpio.pi()
            if not pi.connected:
                logger.warning(
                    "pigpiod not running; motor control disabled (start it with 'sudo pigpiod')"
                )
                return

            pi.set_mode(GPIO_STBY, pigpio.OUTPUT)
            pi.set_mode(GPIO_BIN1, pigpio.OUTPUT)
            pi.set_mode(GPIO_BIN2, pigpio.OUTPUT)
            pi.write(GPIO_STBY, 1)  # take the driver out of standby
            pi.write(GPIO_BIN1, 0)
            pi.write(GPIO_BIN2, 0)
            pi.hardware_PWM(GPIO_PWMB, PWM_FREQUENCY_HZ, 0)
        except Exception as e:
            logger.warning(f"Motor GPIO init failed; motor control disabled: {e}")
            return

        _pi = pi
        _hardware_available = True


def _drive_locked(speed_percent, direction):
    """Set direction pins + PWM duty for continuous drive. Caller holds _lock."""
    global _current_speed_percent, _current_direction, _drive_stopped_at
    speed_percent = max(0.0, min(100.0, speed_percent))

    if speed_percent == 0 and _current_speed_percent > 0:
        _drive_stopped_at = time.monotonic()

    if _hardware_available:
        if speed_percent == 0:
            _pi.write(GPIO_BIN1, 0)
            _pi.write(GPIO_BIN2, 0)
        elif direction == DIRECTION_CW:
            _pi.write(GPIO_BIN1, 1)
            _pi.write(GPIO_BIN2, 0)
        else:
            _pi.write(GPIO_BIN1, 0)
            _pi.write(GPIO_BIN2, 1)
        duty = int(speed_percent / 100.0 * 1_000_000)
        _pi.hardware_PWM(GPIO_PWMB, PWM_FREQUENCY_HZ, duty)

    _current_speed_percent = speed_percent
    _current_direction = direction


def _brake_locked():
    """Short brake per the TB6612 truth table (BIN1=BIN2=H). Caller holds _lock."""
    global _current_speed_percent
    if _hardware_available:
        _pi.write(GPIO_BIN1, 1)
        _pi.write(GPIO_BIN2, 1)
        _pi.hardware_PWM(GPIO_PWMB, PWM_FREQUENCY_HZ, 0)
    _current_speed_percent = 0.0


def spin(speed_percent=None, direction=DIRECTION_CW):
    """Drive the motor continuously at speed_percent (0-100) in direction.

    Speeds under HOP_START_THRESHOLD_PERCENT get a brief kick to
    HOP_START_SPEED_PERCENT first, then settle to the requested speed, since
    rotor position at rest can otherwise stall a slow start.
    """
    _init_hardware()
    if speed_percent is None:
        speed_percent = DEFAULT_SPIN_SPEED_PERCENT
    speed_percent = max(0.0, min(100.0, speed_percent))

    needs_hop = 0 < speed_percent < HOP_START_THRESHOLD_PERCENT

    global _generation
    with _lock:
        _generation += 1
        my_generation = _generation
        _drive_locked(HOP_START_SPEED_PERCENT if needs_hop else speed_percent, direction)

    if needs_hop:
        def _settle():
            time.sleep(HOP_START_DURATION_S)
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
    """Fast stop via TB6612 short brake (BIN1=BIN2=H) - quicker than stop(),
    but safer than just cutting PWM/STBY."""
    _init_hardware()

    global _generation
    with _lock:
        _generation += 1
        _brake_locked()


def step(movement_direction, speed_percent=STEP_SPEED_PERCENT, duration_s=STEP_DURATION_S):
    """Fire a brief pulse opposite movement_direction - the physical "click"
    haptic_feedback uses to push back against the user's spin. Non-blocking,
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
