# Pi deployment

Two systemd units. `spotifywheel.service` runs the Flask backend on port
5001; `kiosk.service` runs Cog against it once it answers.

Both assume the repo is at `/home/pi/spotifyWheel` and the user is `pi`. If
either differs, edit the paths in the unit files before installing.

## Turn the desktop off

The kiosk holds DRM master directly. Any desktop session (labwc under
lightdm) takes DRM master first and Cog dies with `failed to schedule a page
flip: Permission denied`, so the desktop has to be off — not just stopped
once, but disabled:

```sh
sudo systemctl set-default multi-user.target   # boot to console, not desktop
sudo systemctl disable lightdm                 # don't let it come back
sudo systemctl stop lightdm                    # if it's running right now
```

`/api/wake` no longer starts lightdm (it used to, which killed the kiosk the
first time the display woke). Nothing in the app touches the desktop now.

## Install

```sh
sudo cp deploy/spotifywheel.service deploy/kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spotifywheel.service kiosk.service
```

Prerequisites, all covered in [../RUN.md](../RUN.md): the venv built on the
Pi's own SD card (`cd backend && make venv`), `backend/.env` and
`backend/.cache` recreated, and `frontend/dist` present in the checkout.

## Display power

`/api/sleep` and `/api/wake` run the command in `DISPLAY_POWER_COMMAND`
(`backend/backend/power_state.py`), defaulting to
`vcgencmd display_power 0|1`. That needs no sudo — the `pi` user is in the
`video` group by default.

**This is best-effort.** Under Bookworm's full KMS driver (`vc4-kms-v3d`,
the default) `vcgencmd display_power` is a no-op on some setups. Check it
on the real panel:

```sh
vcgencmd display_power 0   # panel should go dark
vcgencmd display_power 1
```

If the panel stays lit, point the env var at something that does work rather
than editing the code — add to `spotifywheel.service`:

```
Environment=DISPLAY_POWER_COMMAND=/usr/local/bin/panel-power {state}
```

`{state}` is substituted with `1` (on) or `0` (off).

Either way the screen still goes black: the frontend reads `/api/status` and
renders nothing but black while asleep, so sleep never depends on the panel
command succeeding. A tap on the dark screen wakes it. What the command buys
you is the panel actually powering down rather than displaying black.

## Checking on it

```sh
systemctl status spotifywheel.service kiosk.service
journalctl -u spotifywheel.service -f
journalctl -u kiosk.service -f
```

Restart just the UI after a `git pull` (remember `frontend/dist` has to have
been rebuilt before the commit):

```sh
sudo systemctl restart spotifywheel.service kiosk.service
```
