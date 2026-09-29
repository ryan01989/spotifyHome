import logging
import os
import threading
import time

from backend.physical_control import encoder, motor_control

logger = logging.getLogger(__name__)

# "Relatively still": the position swing (degrees) allowed within
# HALT_TIME_SEC for the wheel to count as halted. Defaults to one encoder
# step of jitter tolerance.
HALT_DEADZONE_DEGREES = float(os.getenv("VOLUME_HALT_DEADZONE_DEGREES", encoder.DEGREES_PER_STEP))
HALT_TIME_SEC = float(os.getenv("VOLUME_HALT_TIME_SEC", 0.5))

VOLUME_MIN = 0
VOLUME_MAX = 100

# Virtual volume knob spans 1/4 of the wheel's circumference. Angular
# position is all the encoder gives us, so that's expressed as a quarter
# turn (90 degrees) rather than a physical length. The user's position on
# the knob is volume/VOLUME_MAX along this arc - see get_knob_position_degrees().
KNOB_ARC_FRACTION_OF_CIRCUMFERENCE = float(os.getenv("VOLUME_KNOB_ARC_FRACTION", 0.25))
KNOB_ARC_DEGREES = 360.0 * KNOB_ARC_FRACTION_OF_CIRCUMFERENCE
DEGREES_PER_VOLUME_UNIT = KNOB_ARC_DEGREES / VOLUME_MAX

DEFAULT_VOLUME = int(os.getenv("VOLUME_DEFAULT", 50))

MONITOR_POLL_INTERVAL_SEC = float(os.getenv("VOLUME_MONITOR_POLL_INTERVAL_SEC", 0.02))

# Once the user has left the wheel alone this long, hand it back to the motor:
# spin it up again and stop reading rotation as volume input.
RESTART_IDLE_SEC = float(os.getenv("VOLUME_RESTART_IDLE_SEC", 2.0))

_lock = threading.Lock()
_init_lock = threading.Lock()
_monitor_started = False

_current_volume = float(DEFAULT_VOLUME)

# Invoked with the new int volume whenever a wheel spin pushes it across a
# whole unit - lets spotify_api.py push that to the actual device without
# this module knowing anything about Spotify.
_volume_change_listener = None

# Volume tracking runs only between a halt arming it and RESTART_IDLE_SEC of
# stillness disarming it again - see _monitor_loop. Outside that window the
# wheel is the motor's, and motor-driven rotation must not move the volume.
_armed = False
_last_position = None
_last_movement_time = 0.0

# Where the wheel settled, and when it last left the deadzone around that
# point. Seeded as "still since forever" with _was_halted already True, so a
# wheel that is merely sitting there at startup isn't read as the user having
# just halted it - arming takes real movement followed by a stop.
_rest_position = None
_stillness_since = 0.0
_was_halted = True


def _apply_delta_locked(delta_degrees):
    """Advance volume by delta_degrees of wheel rotation, firing one haptic
    click per whole volume unit crossed. Caller holds _lock. Returns the new
    rounded volume if it crossed at least one unit, else None."""
    global _current_volume
    if delta_degrees == 0:
        return None

    previous_volume = _current_volume
    new_volume = previous_volume + delta_degrees / DEGREES_PER_VOLUME_UNIT
    new_volume = max(VOLUME_MIN, min(VOLUME_MAX, new_volume))
    _current_volume = new_volume

    # Encoder's positive direction is assumed wired to match motor_control's
    # DIRECTION_CW (+1) / DIRECTION_CCW (-1) - verify against real hardware,
    # same caveat as encoder.py's NUM_POLES TODO.
    direction = motor_control.DIRECTION_CW if delta_degrees > 0 else motor_control.DIRECTION_CCW
    units_crossed = abs(int(new_volume) - int(previous_volume))
    for _ in range(units_crossed):
        motor_control.step(direction)

    return round(new_volume) if units_crossed else None


def _monitor_loop():
    global _last_position, _last_movement_time, _was_halted, _armed
    global _rest_position, _stillness_since
    while True:
        position = encoder.get_position()
        now = time.monotonic()
        changed_volume = None

        with _lock:
            # "Relatively still" means the wheel hasn't left a deadzone around
            # where it came to rest. Any excursion past that moves the
            # reference and restarts the clock, so HALT_TIME_SEC always
            # measures uninterrupted stillness.
            if _rest_position is None:
                _rest_position = position
            elif abs(position - _rest_position) > HALT_DEADZONE_DEGREES:
                _rest_position = position
                _stillness_since = now
            halted = now - _stillness_since >= HALT_TIME_SEC

            if halted and not _was_halted:
                motor_control.stop()
                _armed = True
                _last_position = position  # start volume tracking fresh from the halt
                _last_movement_time = now
            _was_halted = halted

            if _armed:
                delta = position - _last_position
                _last_position = position
                if delta != 0:
                    _last_movement_time = now
                    changed_volume = _apply_delta_locked(delta)
                elif now - _last_movement_time >= RESTART_IDLE_SEC:
                    # Disarming here is what makes the restart safe: the
                    # rotation the motor is about to produce is its own, and
                    # would otherwise read as the user spinning the volume up.
                    motor_control.spin()
                    _armed = False

        # Fired outside the lock - it may end up making a network call
        # (spotify_api.py's listener) and must not block the poll loop.
        if changed_volume is not None and _volume_change_listener is not None:
            try:
                _volume_change_listener(changed_volume)
            except Exception:
                # A listener blowing up - no Spotify token, network down -
                # must not take the wheel with it. This thread is the only
                # thing reading the encoder, and it doesn't get restarted.
                logger.exception("Volume change listener failed")

        time.sleep(MONITOR_POLL_INTERVAL_SEC)


def _init():
    """Start the background monitor thread on first use; lazy so importing
    this module never blocks or spins up a thread until something actually
    needs it (mirrors encoder.py / motor_control.py's lazy hardware init)."""
    global _monitor_started
    if _monitor_started:
        return
    with _init_lock:
        if _monitor_started:
            return
        _monitor_started = True
        threading.Thread(target=_monitor_loop, daemon=True).start()


def set_volume_change_listener(callback):
    """Register callback(volume:int), invoked (off the monitor thread's lock)
    whenever a wheel spin pushes the tracked volume across a whole unit.
    spotify_api.py uses this to push the change out to the actual device."""
    global _volume_change_listener
    _volume_change_listener = callback


def get_current_volume():
    """Current volume (0-100). Used by spotify_api.py to push volume changes
    to Spotify, and by the frontend to display the level."""
    _init()
    with _lock:
        return round(_current_volume)


def set_current_volume(volume):
    """Seed/resync the tracked volume, e.g. from Spotify's actual device
    volume at startup or after an out-of-band change (phone, web player)."""
    _init()
    global _current_volume
    with _lock:
        _current_volume = max(VOLUME_MIN, min(VOLUME_MAX, float(volume)))


def get_knob_position_degrees():
    """Where the virtual volume knob currently sits along its
    KNOB_ARC_DEGREES arc, derived from the current volume."""
    _init()
    with _lock:
        return (_current_volume / VOLUME_MAX) * KNOB_ARC_DEGREES
