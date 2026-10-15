import json
import logging
import os
import threading
from pathlib import Path

from backend.physical_control import encoder, volume

logger = logging.getLogger(__name__)

# Measured steps-per-revolution lives next to the Spotify token cache in
# backend/, by absolute path for the same reason: relative to the working
# directory, launching from the wrong place would look uncalibrated.
DEFAULT_CALIBRATION_PATH = Path(__file__).resolve().parent.parent.parent / ".wheel_calibration.json"

# What the ring should measure (encoder.py's default, 32). A hand turn rarely
# lands exactly on an 11.25deg step, so within EXPECTED_TOLERANCE_STEPS counts
# as a match and saves the expected value. Further off means a missed magnet
# or a loose sensor - finish() asks before saving that.
EXPECTED_STEPS_PER_REV = encoder.STEPS_PER_REV
EXPECTED_TOLERANCE_STEPS = 1

# Fewer steps than this for "one full turn" means the encoder isn't really
# counting (or the wheel barely moved) - refuse rather than save garbage.
MIN_STEPS_PER_REV = 4

# The prompt appears on every startup, but an unattended reboot of a wall
# display shouldn't sit on it forever: it dismisses itself after this long
# unless calibration was started, keeping the saved (or default) scale.
PROMPT_TIMEOUT_SEC = float(os.getenv("WHEEL_CALIBRATION_PROMPT_TIMEOUT_SEC", 60))

_lock = threading.Lock()
_saved_steps_per_rev = None  # None until calibrated (saved or this session)
_prompt_pending = False
_start_steps = None  # raw encoder count at start(); None when not calibrating


class CalibrationError(Exception):
    pass


class CalibrationMismatch(CalibrationError):
    """Counted a plausible revolution that isn't EXPECTED_STEPS_PER_REV."""

    def __init__(self, steps):
        super().__init__(f"Counted {steps} steps, expected {EXPECTED_STEPS_PER_REV}")
        self.steps = steps


def get_calibration_path():
    return Path(os.getenv("WHEEL_CALIBRATION_PATH") or DEFAULT_CALIBRATION_PATH)


def load():
    """Apply any saved calibration and raise the startup prompt. Called once
    from create_app(). The wheel is calibration's (motor off, no volume
    input) until the prompt is completed, skipped or times out."""
    global _saved_steps_per_rev, _prompt_pending
    path = get_calibration_path()
    try:
        steps_per_rev = int(json.loads(path.read_text())["steps_per_rev"])
    except FileNotFoundError:
        steps_per_rev = None
    except (ValueError, KeyError, TypeError) as e:
        logger.warning(f"Ignoring unreadable wheel calibration at {path}: {e}")
        steps_per_rev = None

    with _lock:
        if steps_per_rev is not None and steps_per_rev >= MIN_STEPS_PER_REV:
            _saved_steps_per_rev = steps_per_rev
            encoder.set_steps_per_rev(steps_per_rev)
        _prompt_pending = True
        volume.suspend()

    timer = threading.Timer(PROMPT_TIMEOUT_SEC, _prompt_timed_out)
    timer.daemon = True
    timer.start()


def _prompt_timed_out():
    with _lock:
        if not _prompt_pending or _start_steps is not None:
            return
    logger.info("Calibration prompt timed out; keeping current calibration")
    skip()


def status():
    """Snapshot for GET /api/calibration. With no encoder hardware (the dev
    Mac) there's nothing to calibrate, so the prompt dismisses itself."""
    available = encoder.is_available()
    if not available:
        skip()

    with _lock:
        in_progress = _start_steps is not None
        return {
            "available": available,
            "calibrated": _saved_steps_per_rev is not None,
            "steps_per_rev": encoder.STEPS_PER_REV,
            "expected_steps_per_rev": EXPECTED_STEPS_PER_REV,
            "prompt": _prompt_pending,
            "in_progress": in_progress,
            # Live count while turning, so the screen can show it's working.
            "steps": abs(encoder.get_steps() - _start_steps) if in_progress else None,
        }


def start():
    """Begin (or restart) counting: the user turns the wheel one revolution."""
    global _start_steps
    if not encoder.is_available():
        raise CalibrationError("No wheel encoder detected")
    with _lock:
        volume.suspend()  # already suspended from the startup prompt
        _start_steps = encoder.get_steps()


def finish(force=False):
    """Save the steps counted since start() as one revolution. Raises
    CalibrationMismatch (still in progress) when the count is off from
    EXPECTED_STEPS_PER_REV, unless force."""
    global _saved_steps_per_rev, _start_steps, _prompt_pending
    with _lock:
        if _start_steps is None:
            raise CalibrationError("Calibration wasn't started")
        steps = abs(encoder.get_steps() - _start_steps)
        if steps < MIN_STEPS_PER_REV:
            raise CalibrationError(
                f"Only counted {steps} steps - turn the wheel one full revolution"
            )
        if abs(steps - EXPECTED_STEPS_PER_REV) <= EXPECTED_TOLERANCE_STEPS:
            steps = EXPECTED_STEPS_PER_REV
        elif not force:
            raise CalibrationMismatch(steps)

        _saved_steps_per_rev = steps
        _start_steps = None
        _prompt_pending = False
        encoder.set_steps_per_rev(steps)

    if steps != EXPECTED_STEPS_PER_REV:
        logger.warning(
            f"Saved wheel calibration of {steps} steps/rev, expected "
            f"{EXPECTED_STEPS_PER_REV} - check the ring's magnets and sensors"
        )

    path = get_calibration_path()
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"steps_per_rev": steps}))
        tmp.replace(path)
    except OSError as e:
        # Still applies for this run; it just won't survive a restart.
        logger.warning(f"Couldn't save wheel calibration to {path}: {e}")

    logger.info(f"Wheel calibrated: {steps} steps per revolution")
    volume.resume()


def skip():
    """Dismiss the prompt (or abandon a calibration in progress), keeping
    whatever calibration was already in effect."""
    global _start_steps, _prompt_pending
    with _lock:
        _start_steps = None
        _prompt_pending = False
    volume.resume()
