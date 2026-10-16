# spotifyWheel — backend

Flask app. Talks to Spotify via Spotipy, serves the built frontend, and owns
the GPIO hardware (wheel encoder, motor, display power).

Scaffolded from `candidtim/cookiecutter-flask-minimal` — app factory in
`backend/__init__.py`, one blueprint in `backend/routes.py`.

Run it: see [../RUN.md](../RUN.md). Short version — `make run` serves
http://localhost:5001.

> Port 5001, not Flask's default 5000: macOS's AirPlay Receiver squats 5000
> and returns 403 to everything.

## Layout

| File | Does |
| --- | --- |
| `__init__.py` | App factory. Points `static_folder` at `../../frontend/dist`. |
| `routes.py` | Every endpoint, all under `/api`. |
| `spotify_api.py` | Spotipy client, auth, playback reads/writes. |
| `authorize.py` | The one-off OAuth flow. Run by hand, never by the app. |
| `power_state.py` | Display sleep/wake. |
| `physical_control/` | `encoder.py` (wheel position), `motor_control.py` (TB6612), `volume.py` (wheel-as-volume-knob). |
| `defaults.py` | Config defaults. Override with `FLASK_`-prefixed env vars. |

## Endpoints

All under `/api`.

**Playback** — `GET /now_playing` returns `{prev, now_playing, next}`, three
flat track objects (`cover`/`name`/`artist`/`progress_ms`/`duration_ms`/
`is_playing`) sourced from Spotify's recently-played, currently-playing and
queue endpoints. `prev`/`next` are best-effort predictions; the queue doesn't
always match what actually plays next under shuffle or repeat.

`POST /next_track`, `/previous_track`, `/pause_playback`, `/resume_playback`
return `{success, now_playing}`, or 502 if Spotify didn't come back.

The skip routes poll Spotify for up to ~750ms after issuing the skip
(`_get_now_playing_after_change`), because Spotify's currently-playing state
lags the acknowledged skip and would otherwise report the track you just left.

**Auth** — `GET /auth_status` → `{authorized}`. Cheap, never prompts.

**Power** — `GET /status` → `{awake}`; `POST /wake`, `POST /sleep`.

**Wheel** — `GET /volume`, `/wheel_position`, `/wheel_velocity`,
`/wheel_acceleration`; `POST /wheel_spin` `{speed_percent, direction}`,
`/wheel_stop`, `/wheel_stop_immediately`, `/wheel_step` `{direction}`.
The motor also follows playback on its own: every Spotify playback read calls
`volume.set_playing()`, which spins it while music plays and stops it on pause.

**Calibration** — `GET /calibration` → `{available, calibrated, steps_per_rev,
expected_steps_per_rev, prompt, in_progress, steps}`; `POST /calibration/start`,
`/calibration/finish` `{force}`, `/calibration/skip`. Every startup raises a
prompt and keeps the motor off until it's finished, skipped, or times out
(`WHEEL_CALIBRATION_PROMPT_TIMEOUT_SEC`, default 60). The user turns the wheel
one revolution; within ±1 step of the expected 32 saves 32, further off returns
409 `mismatch` unless `force`. Saved to `backend/.wheel_calibration.json`
(override with `WHEEL_CALIBRATION_PATH`).

## Spotify auth

The app **never** runs the OAuth flow. On the Pi it's a systemd service with
no browser and no tty, so Spotipy's default behaviour — open a browser, block
on `input()` — would hang a request handler instead of authorizing anything.

`_HeadlessSpotifyOAuth` overrides `get_auth_response()` to refuse outright.
Callers get `SpotifyAuthRequired`, which `routes.py` turns into:

```
503 {"error": "spotify_auth_required", "message": ..., "auth_url": ...}
```

plus a log line naming the fix. Authorize out-of-band instead:

```sh
cd backend && .venv/bin/python -m backend.authorize      # --force to redo
```

It prints a URL, you approve it on any machine, and paste the redirect back.
Works fine over SSH.

Two gotchas this design exists to prevent:

- **Cache location.** A bare `CacheFileHandler()` resolves `.cache` against
  the *current working directory*, so launching from elsewhere silently looks
  like a fresh, never-authorized install. It's anchored to `backend/` instead.
  This is why `spotifywheel.service` sets `WorkingDirectory`.
- **Thread races.** One shared auth manager, not a new `SpotifyOAuth` per
  call — each instantiation re-reads the cache independently, and threaded
  polling makes overlapping requests race on token refresh.

`backend/.env` (client ID/secret/redirect URI) and `backend/.cache` (the
token) are gitignored and must be recreated per machine. The redirect URI
must exactly match one registered in your Spotify app dashboard.

## Display power

`POST /sleep` and `/wake` run `DISPLAY_POWER_COMMAND`, defaulting to
`vcgencmd display_power {state}`. It deliberately does **not** touch lightdm
or any desktop — the kiosk holds DRM master directly, and starting a desktop
to "wake" would steal it and kill Cog.

It's best-effort and fail-soft (no `vcgencmd` on the dev Mac; possibly a no-op
under full KMS). The frontend blanks itself from `/api/status` regardless, so
sleep works either way. Details and overrides:
[../deploy/README.md](../deploy/README.md).

## GPIO is optional

`encoder.py` and `motor_control.py` initialize hardware lazily and fail soft,
so importing them on a machine with no GPIO — the dev Mac — leaves their
commands as no-ops instead of crashing the app. You'll see one of:

- `Motor GPIO init failed; motor control disabled: ...`
- `gpiozero not available; wheel encoder disabled`

Both drive GPIO through gpiozero on lgpio (apt's `python3-lgpio`, visible
because `make venv` builds with `--system-site-packages`). The motor's speed
pin is kernel hardware PWM through sysfs instead, which needs
`dtoverlay=pwm,pin=18,func=2` in `/boot/firmware/config.txt`. No daemon.

The `/api/wheel_*` routes still respond; they just don't move anything.

`volume.py` runs a background monitor thread that watches the encoder and
translates wheel rotation into volume, firing a `motor_control.step()` pulse
per unit as haptic feedback and pushing the new level to Spotify through a
callback. It arms only after the wheel has been still for ~0.5s.

## Make targets

```sh
make venv     # create .venv and install this package editable, with dev extras
make run      # dev server, debug mode, port 5001
make test     # pytest
make clean    # remove venv and build artifacts
make dist     # build a wheel (runs tests first)
```

Add runtime dependencies under `project.dependencies` in `pyproject.toml`,
dev ones under `project.optional-dependencies`; then `make clean && make venv`.

Config defaults live in `backend/defaults.py` and are overridden by
`FLASK_`-prefixed env vars (values are JSON-parsed). `defaults.py` sets
`DEBUG = True` for local dev — `deploy/spotifywheel.service` sets
`FLASK_DEBUG=0` so the kiosk doesn't serve tracebacks.

## Deployment

Systemd units and Pi setup: [../deploy/README.md](../deploy/README.md).

Keep `backend/` and `frontend/` as siblings — Flask resolves the bundle as
`backend/backend/../../../frontend/dist`, so deploying `backend/` alone
breaks static serving.
