import logging
import os
import threading
import time

from backend.physical_control import encoder, motor_control

logger = logging.getLogger(__name__)

# "Relatively still": the position swing (degrees) allowed within
# HALT_TIME_SEC for the wheel to count as halted. Defaults to one encoder
# step of jitter tolerance - read live, since calibration rescales the step.
_HALT_DEADZONE_OVERRIDE = os.getenv("VOLUME_HALT_DEADZONE_DEGREES")
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

# After the wheel changes the volume, ignore the device's reported volume for
# this long. 
DEVICE_SYNC_HOLD_SEC = float(os.getenv("VOLUME_DEVICE_SYNC_HOLD_SEC", 6.0))

# Stall: the motor has been told to spin but the wheel hasn't left the halt
# deadzone in this long. Stop and retry.
STALL_SEC = float(os.getenv("MOTOR_STALL_SEC", 1.5))
STALL_RETRY_SEC = float(os.getenv("MOTOR_STALL_RETRY_SEC", 10.0))

# Pushes to the volume listener go out on their own thread, coalesced to the
# latest value, at most one per PUSH_INTERVAL_SEC. 
PUSH_INTERVAL_SEC = float(os.getenv("VOLUME_PUSH_INTERVAL_SEC", 0.5))

_lock = threading.Lock()
_init_lock = threading.Lock()
_monitor_started = False

_current_volume = float(DEFAULT_VOLUME)
_last_wheel_change_time = None  # monotonic; when the wheel last moved the volume

# Invoked with the new int volume whenever a wheel spin pushes it across a
# whole unit - lets spotify_api.py push that to the actual device without
# this module knowing anything about Spotify. Called from _push_loop.
_volume_change_listener = None
_push_condition = threading.Condition()
_pending_push = None

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

# Whether the motor should be driving when the wheel is the motor's turn -
# true while Spotify reports playback, via set_playing().
_motor_wanted = False

# While suspended (calibration.py has the wheel), nothing here reads the
# encoder or touches the motor; set_playing() still records what's wanted.
_suspended = False

# When this module last told the motor to spin (None while it isn't meant to
# be driving), and when to try again after a stall.
_spin_commanded_at = None
_stall_retry_at = None

# Which way the encoder counts (+1/-1) while the motor drives CW. It's down to
# how the sensors and motor are mounted, and getting it wrong turns every
# haptic click into a push *with* the user instead of against them. Learned
# from the motor's own spins (see _note_motor_motion_locked); the env value
# is only the guess until the first spin. Takes SIGN_LEARN_STREAK same-sign
# excursions in a row, so a hand briefly dragging against the motor can't
# flip it.
_encoder_sign_for_cw = int(os.getenv("ENCODER_SIGN_FOR_CW", 1))
SIGN_LEARN_STREAK = 3
_sign_candidate = 0
_sign_streak = 0


def _halt_deadzone_degrees():
    if _HALT_DEADZONE_OVERRIDE is not None:
        return float(_HALT_DEADZONE_OVERRIDE)
    return encoder.DEGREES_PER_STEP


def _spin_locked(now):
    """Start the motor and the stall watch. Caller holds _lock."""
    global _spin_commanded_at, _stall_retry_at
    motor_control.spin()
    _spin_commanded_at = now
    _stall_retry_at = None


def _stop_locked():
    """Stop the motor and drop any stall watch/retry. Caller holds _lock."""
    global _spin_commanded_at, _stall_retry_at
    motor_control.stop()
    _spin_commanded_at = None
    _stall_retry_at = None


def _note_motor_motion_locked(delta_degrees):
    """Record which way the encoder moved while the motor drove CW (every
    spin this module commands is CW). Caller holds _lock."""
    global _encoder_sign_for_cw, _sign_candidate, _sign_streak
    sign = 1 if delta_degrees > 0 else -1
    if sign == _sign_candidate:
        _sign_streak += 1
    else:
        _sign_candidate, _sign_streak = sign, 1
    if _sign_streak >= SIGN_LEARN_STREAK and sign != _encoder_sign_for_cw:
        logger.info(f"Encoder counts {'+' if sign > 0 else '-'} when the motor drives CW; clicks follow that")
        _encoder_sign_for_cw = sign


def _apply_delta_locked(delta_degrees):
    """Advance volume by delta_degrees of wheel rotation, firing one haptic
    click per whole volume unit crossed. Caller holds _lock. Returns the new
    rounded volume if it crossed at least one unit, else None."""
    global _current_volume, _last_wheel_change_time
    if delta_degrees == 0:
        return None

    previous_volume = _current_volume
    new_volume = previous_volume + delta_degrees / DEGREES_PER_VOLUME_UNIT
    new_volume = max(VOLUME_MIN, min(VOLUME_MAX, new_volume))
    _current_volume = new_volume
    _last_wheel_change_time = time.monotonic()

    # The wheel's motion in motor terms, from the encoder's sign: step()
    # pushes opposite it, i.e. against what the encoder detected.
    moving_cw = (delta_degrees > 0) == (_encoder_sign_for_cw > 0)
    direction = motor_control.DIRECTION_CW if moving_cw else motor_control.DIRECTION_CCW
    units_crossed = abs(int(new_volume) - int(previous_volume))
    for _ in range(units_crossed):
        motor_control.step(direction)

    return round(new_volume) if units_crossed else None


def _tick(position, now):
    """One monitor poll: halt/arm/disarm, volume tracking, stall watch."""
    global _last_position, _last_movement_time, _was_halted, _armed
    global _rest_position, _stillness_since, _spin_commanded_at, _stall_retry_at
    changed_volume = None

    with _lock:
        if _suspended:
            return  # suspended since the caller checked

        # "Relatively still" means the wheel hasn't left a deadzone around
        # where it came to rest. Any excursion past that moves the
        # reference and restarts the clock, so HALT_TIME_SEC always
        # measures uninterrupted stillness.
        if _rest_position is None:
            _rest_position = position
        elif abs(position - _rest_position) > _halt_deadzone_degrees():
            if not _armed and _spin_commanded_at is not None:
                _note_motor_motion_locked(position - _rest_position)
            _rest_position = position
            _stillness_since = now
        halted = now - _stillness_since >= HALT_TIME_SEC

        # Already armed, a halt is just the user pausing mid-turn - they still
        # have the knob, and the motor is already off.
        if halted and not _was_halted and not _armed:
            logger.info("Wheel halted; volume knob armed, motor stopped")
            _stop_locked()
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
                # Paused, the wheel just goes back to sitting still.
                logger.info(f"Volume knob idle; disarmed (motor {'spinning' if _motor_wanted else 'stays off'})")
                if _motor_wanted:
                    _spin_locked(now)
                _armed = False
        elif _spin_commanded_at is not None:
            if now - max(_stillness_since, _spin_commanded_at) >= STALL_SEC:
                logger.warning(
                    f"Motor stalled: driving at {motor_control.DEFAULT_SPIN_SPEED_PERCENT:g}% "
                    f"but the wheel hasn't moved in {STALL_SEC:g}s. Stopping; retrying in "
                    f"{STALL_RETRY_SEC:g}s. Raise MOTOR_SPIN_SPEED_PERCENT - "
                    "motor_test.py --sweep measures what it needs."
                )
                _stop_locked()
                _stall_retry_at = now + STALL_RETRY_SEC
        elif _stall_retry_at is not None and now >= _stall_retry_at:
            _spin_locked(now)

    if changed_volume is not None:
        _queue_push(changed_volume)


def _monitor_loop():
    while True:
        if not _suspended:
            _tick(encoder.get_position(), time.monotonic())
        time.sleep(MONITOR_POLL_INTERVAL_SEC)


def _queue_push(volume):
    global _pending_push
    with _push_condition:
        _pending_push = volume
        _push_condition.notify()


def _push_loop():
    global _pending_push
    while True:
        with _push_condition:
            while _pending_push is None:
                _push_condition.wait()
            volume, _pending_push = _pending_push, None
        if _volume_change_listener is not None:
            try:
                _volume_change_listener(volume)
            except Exception:
                # A listener blowing up - no Spotify token, network down -
                # must not take volume pushes with it; this thread doesn't
                # get restarted.
                logger.exception("Volume change listener failed")
        time.sleep(PUSH_INTERVAL_SEC)


def _init():
    """Start the background monitor and push threads on first use; lazy so
    importing this module never blocks or spins up a thread until something
    actually needs it (mirrors encoder.py / motor_control.py's lazy hardware
    init)."""
    global _monitor_started
    if _monitor_started:
        return
    with _init_lock:
        if _monitor_started:
            return
        _monitor_started = True
        threading.Thread(target=_monitor_loop, daemon=True).start()
        threading.Thread(target=_push_loop, daemon=True).start()


def set_playing(is_playing):
    """Spin the wheel while music plays, stop it on pause. spotify_api.py
    calls this on every playback read; only a change acts. While the user
    holds the knob (armed) or calibration has the wheel, it's just recorded -
    disarming/resume() pick it up when the wheel comes back to the motor."""
    _init()
    global _motor_wanted
    with _lock:
        if is_playing == _motor_wanted:
            return
        _motor_wanted = is_playing
        if _armed or _suspended:
            logger.info(
                f"Playback {'started' if is_playing else 'paused'}; motor change held "
                f"while {'calibrating' if _suspended else 'the knob is armed'}"
            )
            return
        logger.info(f"Playback {'started' if is_playing else 'paused'}; motor {'spinning' if is_playing else 'stopping'}")
        if is_playing:
            _spin_locked(time.monotonic())
        else:
            _stop_locked()


def suspend():
    """Hand the wheel to calibration: stop the motor, drop out of volume
    mode, and stop reading the encoder until resume()."""
    global _suspended, _armed
    with _lock:
        # stop() rather than stop_immediately(): it ends coasting, where the
        # short brake would leave the wheel stiff to turn by hand. Only if the
        # motor is actually running 
        if _motor_wanted and not _armed and not _suspended:
            _stop_locked()
        _suspended = True
        _armed = False


def resume():
    """Take the wheel back from calibration, restarting the motor if music
    is playing. Re-seeds the halt detector from scratch - calibration may
    have rescaled get_position(), and the hand-turn that just happened
    mustn't read as a halt that arms the knob."""
    global _suspended, _rest_position, _last_position, _was_halted
    with _lock:
        if not _suspended:
            return
        _suspended = False
        _rest_position = None
        _last_position = None
        _was_halted = True
        logger.info(f"Wheel back from calibration; motor {'spinning' if _motor_wanted else 'stays off (not playing)'}")
        if _motor_wanted:
            _spin_locked(time.monotonic())


def set_volume_change_listener(callback):
    """Register callback(volume:int), invoked from the push thread (coalesced,
    at most once per PUSH_INTERVAL_SEC) when wheel spins move the volume.
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
    volume at startup or after an out-of-band change (phone, web player).
    Ignored while the user has the knob, and for DEVICE_SYNC_HOLD_SEC after
    the wheel last moved the volume: Spotify reports the pre-change level
    for a while, and taking it would snap the knob back."""
    _init()
    global _current_volume
    with _lock:
        if _armed:
            return
        if (_last_wheel_change_time is not None
                and time.monotonic() - _last_wheel_change_time < DEVICE_SYNC_HOLD_SEC):
            return
        _current_volume = max(VOLUME_MIN, min(VOLUME_MAX, float(volume)))


def get_knob_position_degrees():
    """Where the virtual volume knob currently sits along its
    KNOB_ARC_DEGREES arc, derived from the current volume."""
    _init()
    with _lock:
        return (_current_volume / VOLUME_MAX) * KNOB_ARC_DEGREES
