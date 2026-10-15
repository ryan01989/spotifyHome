import logging
import os
import time
from collections import deque
from threading import Lock

logger = logging.getLogger(__name__)

# See hall_encoder_notes.md: sensor A -> GPIO23 (pin 16), sensor B -> GPIO24 (pin 18),
# both wired open-collector-through-10k into the GPIO, internal pull-ups enabled here.
GPIO_PIN_A = int(os.getenv("ENCODER_GPIO_A", 24))
GPIO_PIN_B = int(os.getenv("ENCODER_GPIO_B", 23))

# Magnet poles on the wheel ring: 16 magnets mounted N,S,N,S, so 16 poles.
# Each N-S pair is one quadrature cycle, and counting every edge on both
# sensors gives 4 steps per cycle = 2 steps per pole = 32 steps/rev.
# calibration.py checks that against a measured hand-turned revolution and
# hands the result to set_steps_per_rev().
NUM_POLES = int(os.getenv("ENCODER_NUM_POLES", 16))
STEPS_PER_REV = 2 * NUM_POLES
DEGREES_PER_STEP = 360.0 / STEPS_PER_REV

# Valid quadrature sequence (state = A<<1 | B): 0 -> 1 -> 3 -> 2 -> 0 forward,
# reverse for the other direction. Anything else means a missed/extra edge.
_TRANSITIONS = {
    (0, 1): 1, (1, 3): 1, (3, 2): 1, (2, 0): 1,
    (0, 2): -1, (2, 3): -1, (3, 1): -1, (1, 0): -1,
}

# How far back to look when estimating velocity/acceleration from the raw
# step history, and how long without a new edge before we call the wheel
# stopped (rather than reporting a stale nonzero reading forever).
VELOCITY_WINDOW_SEC = 0.5
ACCEL_WINDOW_SEC = 0.5
STOP_TIMEOUT_SEC = 0.75

_state_lock = Lock()
_init_lock = Lock()
_position_steps = 0
_last_state = None
_position_history = deque()  # (timestamp, position_degrees), newest last
_velocity_history = deque()  # (timestamp, velocity_deg_per_s), newest last

_sensor_a = None
_sensor_b = None
_hardware_available = False
_hardware_attempted = False


def _init_hardware():
    """Set up the GPIO sensors on first use; a no-op (and safe) if no GPIO is present.

    Lazy so importing this module (e.g. via routes.py) never crashes on a
    machine with no GPIO, such as the Mac used for local dev - it just
    leaves the encoder reporting a stationary wheel. Only tried once: this
    can't change at runtime, so retrying on every call would just re-log
    the same failure every time a route reads the encoder.
    """
    global _sensor_a, _sensor_b, _hardware_available, _hardware_attempted
    if _hardware_attempted:
        return

    with _init_lock:
        if _hardware_attempted:
            return
        _hardware_attempted = True

        try:
            from gpiozero import Button
        except ImportError:
            logger.warning("gpiozero not available; wheel encoder disabled")
            return

        try:
            sensor_a = Button(GPIO_PIN_A, pull_up=True)
            sensor_b = Button(GPIO_PIN_B, pull_up=True)
            sensor_a.when_pressed = _on_edge
            sensor_a.when_released = _on_edge
            sensor_b.when_pressed = _on_edge
            sensor_b.when_released = _on_edge
        except Exception as e:
            logger.warning(
                f"Encoder GPIO init failed; wheel encoder disabled: {e}")
            return

        _sensor_a, _sensor_b = sensor_a, sensor_b
        _hardware_available = True


def _prune(history, now, window_sec):
    """Drop entries older than window_sec, always keeping at least 2."""
    while len(history) > 2 and now - history[0][0] > window_sec:
        history.popleft()


def _velocity_locked(now):
    """Velocity in degrees/s from the position history. Caller holds _state_lock."""
    if not _position_history:
        return 0.0
    t_old, p_old = _position_history[0]
    _, p_last = _position_history[-1]
    if now == t_old:
        return 0.0
    return (p_last - p_old) / (now - t_old)


def _on_edge():
    """Quadrature callback: read both sensors, advance position by one step if valid."""
    global _position_steps, _last_state
    state = (int(_sensor_a.is_pressed) << 1) | int(_sensor_b.is_pressed)

    with _state_lock:
        if _last_state is None:
            _last_state = state
            return

        direction = _TRANSITIONS.get((_last_state, state))
        _last_state = state
        if direction is None:
            # Skipped a state - either a missed edge under load, or the two
            # sensors aren't a clean quarter pitch apart. Resync without
            # counting a step for it.
            logger.warning("Invalid quadrature transition, ignoring")
            return

        _position_steps += direction
        now = time.monotonic()
        position_degrees = _position_steps * DEGREES_PER_STEP

        _position_history.append((now, position_degrees))
        _prune(_position_history, now, VELOCITY_WINDOW_SEC)

        velocity = _velocity_locked(now)
        _velocity_history.append((now, velocity))
        _prune(_velocity_history, now, ACCEL_WINDOW_SEC)


def is_available():
    """Whether the encoder hardware came up - False on a machine with no GPIO."""
    _init_hardware()
    return _hardware_available


def get_steps():
    """Raw cumulative step count, independent of the degrees-per-step scale.
    What calibration counts, since that scale is the thing being measured."""
    _init_hardware()
    with _state_lock:
        return _position_steps


def set_steps_per_rev(steps_per_rev):
    """Rescale to a measured steps-per-revolution. get_position() jumps to the
    new scale immediately, so anything tracking position across this call
    (volume.py's monitor) has to re-seed its reference afterwards."""
    global STEPS_PER_REV, DEGREES_PER_STEP
    with _state_lock:
        STEPS_PER_REV = steps_per_rev
        DEGREES_PER_STEP = 360.0 / steps_per_rev
        # Histories are in degrees at the old scale; mixing them with new
        # samples would report a phantom velocity spike.
        _position_history.clear()
        _velocity_history.clear()


def get_position():
    """Wheel position in degrees, cumulative from wherever it was at process start."""
    _init_hardware()
    with _state_lock:
        return _position_steps * DEGREES_PER_STEP


def get_velocity():
    """Wheel velocity in degrees/s. Sign gives direction, 0 if stopped."""
    _init_hardware()
    now = time.monotonic()
    with _state_lock:
        if not _position_history or now - _position_history[-1][0] > STOP_TIMEOUT_SEC:
            return 0.0
        return _velocity_locked(now)


def get_acceleration():
    """Wheel acceleration in degrees/s^2, derived from recent velocity samples."""
    _init_hardware()
    now = time.monotonic()
    with _state_lock:
        if not _velocity_history or now - _velocity_history[-1][0] > STOP_TIMEOUT_SEC:
            return 0.0
        if len(_velocity_history) < 2:
            return 0.0
        t_old, v_old = _velocity_history[0]
        t_new, v_new = _velocity_history[-1]
        if t_new == t_old:
            return 0.0
        return (v_new - v_old) / (t_new - t_old)
