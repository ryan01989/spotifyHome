# spotifyWheel — frontend

React + Vite. Renders the now-playing screen for a **720×720 round** display
driven by the Flask backend in [`../backend`](../backend).

Run it: see [../RUN.md](../RUN.md). Short version — `npm run dev` on :5173
proxies `/api/*` to the backend on :5001.

## Designing for a circle

`.stage` in `App.css` is a fixed 720×720 box with `border-radius: 50%` and
`overflow: hidden`, so development in a rectangular browser window clips to
the same circle the hardware physically shows. Anything past that edge does
not exist on the device — corners are not "slightly cut off", they are gone.

Album art gets the same treatment more aggressively: `warpToCircle.js`
implements an inverse elliptical grid mapping that pulls a square cover into
the disc, so the art fills the circle instead of being center-cropped.
`WarpedCover.jsx` runs it on a canvas and falls back to a plain center crop
if the cover host refuses cross-origin pixel reads.

## Structure

| File | Does |
| --- | --- |
| `main.jsx` | Mounts `SpinProvider` → `NowPlayingProvider` → `App`. |
| `App.jsx` | Layout, optimistic play/pause, swipe wiring, sleep blackout. |
| `NowPlayingContext.jsx` | Polls the backend; owns prev/now/next and awake state. |
| `useSwipeGesture.js` | Touch/pointer gesture recognition (tap, horizontal swipe). |
| `currentTrackInfo.jsx` | Title/artist/progress, with marquee for long titles. |
| `currentTrackCover.jsx` → `WarpedCover.jsx` → `warpToCircle.js` | Cover art, warped to the disc. |
| `VolumeArc.jsx` | Arc along the rim showing wheel-knob volume. |
| `SpinContext.jsx` | Cover spin duration, read from `public/presets.json`. |
| `formatTime.js` | `mm:ss`, plus `useSimulatedProgress`. |

## How the data flows

`NowPlayingContext` polls `GET /api/now_playing` every 5s and gets three flat
track objects back — `{prev, now_playing, next}`. Three things depend on that
shape:

**Progress is simulated between polls.** A 5s poll would make the progress bar
jump in visible steps, so `useSimulatedProgress` ticks locally at 200ms and
re-anchors whenever a real value arrives. It freezes/resumes on play-state
change immediately rather than waiting for the next poll.

**Neighbor covers are preloaded.** `prev`/`next` cover URLs are pushed into
the browser image cache (`new Image().src = …`) as soon as they arrive, so a
swipe never waits on an image decode.

**Swipes are optimistic.** On skip/back, `applyPreloadedTrack()` shows the
already-preloaded neighbor *immediately*, then the real POST goes out and
`reconcileNowPlaying()` corrects the guess when it lands. If nothing is
preloaded yet (right after load) `applyPreloadedTrack` returns `false` and
the caller returns the request promise instead, so the UI waits on the network
rather than showing the track you just swiped away from.

`prev`/`next` are predictions from Spotify's history/queue endpoints. Under
shuffle or repeat they can be wrong — that's what the reconcile is for.

## Sleep

`GET /api/status` reports `{awake}`. While asleep, `App.jsx` renders a black
stage and nothing else, polling drops to a slower status-only cadence, and a
tap anywhere wakes. The backend separately tries to power the panel down, but
that's best-effort — the blackout here is what actually guarantees a dark
screen. See [../deploy/README.md](../deploy/README.md).

Nothing in the UI *triggers* sleep; it's expected to come from outside (an
NFC tag hitting `POST /api/sleep`).

## Touch

`useSwipeGesture` listens on **both** Touch Events and Pointer Events. This
is deliberate: on the Pi's panel, pointer events were observed reporting
`pointerType: "mouse"` with the up-position equal to the down-position no
matter how far you actually dragged, which makes swipe detection impossible.
The raw touch path is the one that works on hardware; the pointer path is for
mouse during dev. `preventDefault()` on touch suppresses the synthetic mouse
events that would otherwise fire both paths for one finger.

## The build is committed

`dist/` is intentionally **not** gitignored — the Pi pulls the prebuilt bundle
from git so node never has to run there.

**Rebuild before every commit that touches `src/`.** Forget and the Pi serves
the old UI silently, with no error anywhere.

```sh
npm run build
```
