import logging
import os
import shlex
import subprocess

logger = logging.getLogger(__name__)

# Command used to power the panel on/off, with {state} substituted as 1/0.
#
# Deliberately does NOT touch lightdm or any desktop session. The kiosk
# (Cog) holds DRM master directly on a console-booted Pi, so starting a
# desktop to "wake" would steal DRM master and kill the kiosk — which is
# what the old `systemctl start lightdm` here did. See deploy/README.md.
#
# vcgencmd needs no sudo for members of the `video` group (the `pi` user is
# one by default). Caveat: under Bookworm's full KMS driver (vc4-kms-v3d)
# display_power is a no-op on some setups. If the panel stays lit on the
# real hardware, point this at something that does work rather than editing
# the code:
#   DISPLAY_POWER_COMMAND="/usr/local/bin/panel-power {state}"
# Sleep does not depend on this succeeding — the frontend blanks itself
# from /api/status either way, so a no-op here still gives a black screen,
# just without the panel actually powering down.
DISPLAY_POWER_COMMAND = os.getenv("DISPLAY_POWER_COMMAND", "vcgencmd display_power {state}")

_awake = True


def is_awake():
    return _awake


def wake():
    global _awake
    _awake = True
    _set_display_power(True)
    return _awake


def sleep():
    global _awake
    _awake = False
    _set_display_power(False)
    return _awake


def _set_display_power(on):
    # Best-effort: vcgencmd doesn't exist on the Mac used for dev, and may
    # no-op under full KMS on the Pi (see DISPLAY_POWER_COMMAND). Neither
    # should fail the request — `_awake` is the source of truth the frontend
    # reads back from /api/status.
    command = shlex.split(DISPLAY_POWER_COMMAND.format(state=1 if on else 0))
    if not command:
        return

    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Display power command unavailable ({command[0]}): {e}")
        return

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        logger.warning(f"Display power command failed ({result.returncode}): {stderr}")
