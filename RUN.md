# Running spotifyWheel

Two ways to run this project:

- **Dev mode** — two servers (Flask + Vite), hot reload. Use while writing code.
- **Single-server mode** — Flask alone, serving a prebuilt frontend bundle.
  This is how it runs on the Pi.

Ports: backend **5001**, Vite dev server **5173**.

> Port 5001, not Flask's default 5000, because macOS's AirPlay Receiver
> permanently occupies 5000 and returns 403 to everything.

---

## Dev mode (two servers)

Terminal 1 — backend:

```sh
cd backend
make run          # http://localhost:5001
```

Terminal 2 — frontend:

```sh
cd frontend
npm run dev       # http://localhost:5173
```

**Open http://localhost:5173** — not 5001. Vite proxies `/api/*` through to
`127.0.0.1:5001` (configured in `frontend/vite.config.js`), so the frontend's
relative fetches resolve against the real backend while you get hot reload.

`make run` creates the venv on first use and runs Flask in debug mode, so
Python changes reload automatically too.

---

## Single-server mode (Flask serves the built frontend)

```sh
cd frontend && npm run build
cd ../backend && make run
```

**Open http://localhost:5001** — the whole app, frontend and API on one origin.

`create_app()` points Flask's `static_folder` at `frontend/dist` with
`static_url_path=""`, so `/` serves the built `index.html` and `/assets/*`, 
`/presets.json`, `/favicon.svg` are served straight out of the bundle.

### The build is committed

`frontend/dist` is deliberately **not** gitignored. The Pi pulls the prebuilt
bundle from git, so node and npm never have to run on it.

The tradeoff: the bundle is only correct if you rebuild before committing.
**Push a frontend change without running `npm run build` and the Pi will serve
the old UI — silently, with no error.** Rebuild as part of every commit that
touches `frontend/src`.

---

## On the Pi

### One-time setup

1. **Clone the repo.** Keep `backend/` and `frontend/` as siblings — Flask
   resolves the bundle as `backend/backend/../../../frontend/dist`, so
   deploying `backend/` alone breaks static serving.

2. **Build the venv on the Pi's own SD card:**

   ```sh
   cd backend && make venv
   ```

   Never run this against the `/Volumes/spotifyWheel` SMB mount — venv creation
   is hundreds of small files, each a network round-trip, and takes 15+ minutes
   there versus ~10s locally.

3. **Recreate the two gitignored secrets** (they are not, and should not be, in
   git):

   - `backend/.env` — Spotify client ID / secret / redirect URI. The redirect
     URI must exactly match one registered in your Spotify app dashboard.
   - `backend/.cache` — Spotipy's OAuth token cache. Either copy it from the
     Mac (`scp`; the refresh token is tied to the client credentials, not the
     machine) or run the flow on the Pi over SSH:

     ```sh
     cd backend && .venv/bin/python -m backend.authorize
     ```

     It prints a URL, you open it on any machine, approve, and paste the URL
     your browser lands on back into the prompt. `--force` re-authorizes even
     when the existing cache still works.

     **The app never runs this flow itself.** Under systemd there's no browser
     and no tty, so prompting would hang a request instead of authorizing
     anything; `spotify_api.py` refuses to prompt and raises
     `SpotifyAuthRequired`, which the API turns into
     `503 {"error": "spotify_auth_required", "auth_url": ...}` and a log line
     naming this command. `GET /api/auth_status` → `{"authorized": bool}` is
     the cheap check for whether the cache is usable (it never prompts either).

4. **Boot to console instead of the desktop**, so nothing holds DRM master:
   `raspi-config` → System Options → Boot / Auto Login → Console.

5. **Enable kernel PWM for the motor and install lgpio.** Add this line to
   `/boot/firmware/config.txt`, then reboot:

   ```
   dtoverlay=pwm,pin=18,func=2
   ```

   Install lgpio with `sudo apt install python3-lgpio`. The venv sees it
   because `make venv` builds with `--system-site-packages`; an older venv
   needs `make clean && make venv`.

### Starting it

Install the two systemd units — see [deploy/README.md](deploy/README.md):

```sh
sudo cp deploy/spotifywheel.service deploy/kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spotifywheel.service kiosk.service
```

That brings up the backend on 5001 and Cog against it on every boot, and
restarts either if it dies.

To run it by hand instead (e.g. while debugging over SSH):

```sh
cd backend && .venv/bin/flask --app backend run --port 5001
cog --platform=drm http://127.0.0.1:5001/
```

`make run` works too, but it enables Flask's debug mode and reloader — fine for
poking at the Pi over SSH, not what you want for kiosk use.

If a desktop session is running, stop it before launching Cog. Stop lightdm,
not labwc — lightdm supervises the session and respawns labwc the moment it
dies, so `pkill labwc` just restarts the desktop:

```sh
sudo systemctl stop lightdm             # until next boot
```

Better: disable it permanently (`deploy/README.md` covers this), since the
units above assume nothing else is holding DRM master:

```sh
sudo systemctl set-default multi-user.target
sudo systemctl disable lightdm
```

---

## GPIO dependencies are optional

`motor_control.py` and `encoder.py` initialize their hardware lazily and fail
soft, so importing them on a machine with no GPIO — the Mac — leaves the motor
and encoder commands as no-ops rather than crashing the app. You'll see one of
these in the log and nothing else breaks:

- `Motor GPIO init failed; motor control disabled: ...`
- `gpiozero not available; wheel encoder disabled`

On the Pi, look for `Motor control ready: pins on LGPIOFactory, kernel PWM
20000Hz on pwmchip0/pwm0` at the first motor command. Any factory other than
`LGPIOFactory` means the venv can't see apt's `python3-lgpio`.

The `/api/wheel_*` endpoints still respond; they just don't move anything.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| 403 from every request on port 5000 | macOS AirPlay Receiver owns that port. Use 5001. |
| `/api/*` 404s in the browser at :5173 | Backend isn't running — Vite's proxy has nothing to forward to. |
| UI is stale after a `git pull` on the Pi | `frontend/dist` wasn't rebuilt before the commit. |
| Cog: `failed to schedule a page flip: Permission denied` | A desktop session (labwc) holds DRM master. `sudo systemctl stop lightdm` (killing labwc alone just respawns it), or boot to console. |
| Cog `--platform=wl` segfaults | Known unresolved issue with labwc on VideoCore IV. Use `--platform=drm` with the desktop stopped. |
| Motor commands do nothing on the Pi | Check the `Motor control ready` / `Motor PWM init failed` log line. No `pwmchip0` → the `dtoverlay=pwm` line is missing from `config.txt` (reboot after adding). Not `LGPIOFactory` → `sudo apt install python3-lgpio` and rebuild the venv (`make clean && make venv`). `GPIO busy` → another process (a bench script, a second backend) holds the pins. |
| `/api/*` returns 503 `spotify_auth_required` | No usable token cache — run `.venv/bin/python -m backend.authorize` in `backend/`. Also check the service's `WorkingDirectory`, since a missing `.cache` looks identical. |

---

## Other make targets

```sh
cd backend
make test     # pytest
make clean    # remove venv and build artifacts
make dist     # build a wheel (runs tests first)
```
