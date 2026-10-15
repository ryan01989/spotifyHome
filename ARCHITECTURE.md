# Architecture

UML views of the current system, as Mermaid. They render on GitHub; in VS Code
they need a Mermaid-capable Markdown preview extension. The rationale behind the
design is in [`CLAUDE.md`](CLAUDE.md); this file just shows the structure.

1. [Deployment](#1-deployment): what runs where
2. [Backend modules](#2-backend-modules): the Python packages and how they depend on each other
3. [Frontend components](#3-frontend-components): the React tree, hooks and HTTP calls
4. [Sequence: swipe to next track](#4-sequence-swipe-to-next-track)
5. [Sequence: wheel as volume knob](#5-sequence-wheel-as-volume-knob)

## 1. Deployment

```mermaid
flowchart LR
  subgraph PI["«device» Raspberry Pi Zero 2 W (Bookworm, multi-user.target, no desktop)"]
    direction TB
    subgraph KIOSK["«systemd» kiosk.service"]
      COG["Cog --platform=drm<br/>(WPE WebKit)<br/>runs frontend/dist bundle"]
    end
    subgraph SVC["«systemd» spotifywheel.service"]
      FLASK["Flask app :5001<br/>serves /api/* + frontend/dist"]
      MON["volume monitor thread"]
      CACHE[("backend/.cache<br/>OAuth token")]
      ENV[("backend/.env")]
    end
    PIGPIOD["«systemd» pigpiod"]
    VCG["vcgencmd display_power"]
    AUTH["«CLI» python -m backend.authorize<br/>(run by hand over SSH)"]
  end

  subgraph HW["«hardware»"]
    PANEL["Waveshare 4in 720×720<br/>round HDMI panel"]
    DRV["TB6612FNG ch.B → N20 motor"]
    HALL["Hall quadrature encoder<br/>GPIO23 / GPIO24"]
  end

  SPOTIFY["«external» Spotify Web API"]
  NFC["«external» NFC tag / phone"]
  MAC["«dev» Mac: Vite :5173<br/>proxies /api → :5001"]

  COG -- "HTTP GET/POST 127.0.0.1:5001" --> FLASK
  COG -- "KMS/DRM via mini-HDMI" --> PANEL
  PANEL -- "USB touch events" --> COG
  FLASK -- "HTTPS (Spotipy)" --> SPOTIFY
  MON -- "HTTPS set volume" --> SPOTIFY
  FLASK --- CACHE
  FLASK --- ENV
  AUTH -- "writes" --> CACHE
  FLASK -- "subprocess" --> VCG
  VCG -. "best-effort power" .-> PANEL
  FLASK -- "pigpio socket" --> PIGPIOD
  MON -- "pigpio socket" --> PIGPIOD
  PIGPIOD -- "20 kHz HW PWM<br/>GPIO4/18/17/27" --> DRV
  HALL -- "gpiozero edge callbacks" --> FLASK
  NFC -- "POST /api/sleep, /api/wake" --> FLASK
  MAC -. "dev only" .-> FLASK
```

`kiosk.service` `Requires=` the backend and blocks in `ExecStartPre` until
`/api/status` answers. `spotifywheel.service` only `Wants=pigpiod`, because
the motor fails soft without it.

## 2. Backend modules

Every backend file is a module of functions plus module-level state, with no
instantiated classes. Each module is drawn as a `«module»` class: `-` marks
private state and helpers, `+` marks the public API. Dashed arrows are import
dependencies.

```mermaid
classDiagram
  direction TB

  class app_factory["backend package (create_app)"] {
    <<module>>
    +FRONTEND_DIST: Path
    +create_app(config_overrides) Flask
  }

  class routes["routes (Blueprint /api)"] {
    <<module>>
    +GET /now_playing
    +GET /current_track
    +GET /status
    +GET /auth_status
    +GET /volume
    +POST /wake
    +POST /sleep
    +POST /pause_playback
    +POST /resume_playback
    +POST /next_track
    +POST /previous_track
    +GET /wheel_position
    +GET /wheel_velocity
    +GET /wheel_acceleration
    +POST /wheel_spin
    +POST /wheel_stop
    +POST /wheel_stop_immediately
    +POST /wheel_step
    +GET /calibration
    +POST /calibration/start|finish|skip
    -handle_spotify_auth_required() 503
  }

  class spotify_api {
    <<module>>
    +SCOPE: str
    +DEFAULT_CACHE_PATH: Path
    -_spotify_client: Spotify
    -_auth_manager: _HeadlessSpotifyOAuth
    +get_auth_manager()
    +get_cached_token(auth_manager) dict
    +is_authorized() bool
    +get_spotify_client() Spotify
    +get_playing(trackType) Track
    +get_current_track() dict
    +get_last_track() dict
    +get_next_track() dict
    +pause_playback() Track
    +resume_playback() Track
    +next_track() Track
    +previous_track() Track
    +set_device_volume(volume_percent)
    -_sync_volume_from_device(playback)
    -_get_now_playing_after_change(previous_name)
  }

  class HeadlessSpotifyOAuth["_HeadlessSpotifyOAuth"] {
    +get_auth_response() raises SpotifyAuthRequired
  }
  class SpotifyOAuth["spotipy.SpotifyOAuth"] {
    <<external>>
  }
  class SpotifyAuthRequired {
    <<exception>>
    +authorize_url: str
  }

  class Track {
    <<JSON>>
    +cover: str
    +name: str
    +artist: str
    +progress_ms: int
    +duration_ms: int
    +is_playing: bool
  }

  class power_state {
    <<module>>
    +DISPLAY_POWER_COMMAND: str
    -_awake: bool
    +is_awake() bool
    +wake() bool
    +sleep() bool
    -_set_display_power(on)
  }

  class authorize {
    <<CLI>>
    +main(argv) int
  }

  namespace physical_control {
    class volume {
      <<module>>
      +KNOB_ARC_DEGREES = 90
      +HALT_TIME_SEC = 0.5
      +RESTART_IDLE_SEC = 2.0
      -_current_volume: float
      -_armed: bool
      -_volume_change_listener: callable
      +set_volume_change_listener(cb)
      +get_current_volume() int
      +set_current_volume(v)
      +get_knob_position_degrees() float
      +set_playing(is_playing) spins/stops motor
      +suspend() / resume()
      -_init() starts monitor thread
      -_monitor_loop()
      -_apply_delta_locked(delta_degrees)
    }
    class encoder {
      <<module>>
      +GPIO_PIN_A = 23
      +GPIO_PIN_B = 24
      +NUM_POLES = 16 (32 steps/rev)
      +set_steps_per_rev(n)
      +get_steps() int
      +DEGREES_PER_STEP: float
      -_position_steps: int
      -_on_edge() gpiozero callback
      +get_position() float
      +get_velocity() float
      +get_acceleration() float
    }
    class motor_control {
      <<module>>
      +DIRECTION_CW = 1
      +DIRECTION_CCW = -1
      +PWM_FREQUENCY_HZ = 20000
      -_generation: int
      +spin(speed_percent, direction)
      +stop() ramped
      +stop_immediately() short brake
      +step(movement_direction) haptic click
    }
  }

  SpotifyOAuth <|-- HeadlessSpotifyOAuth
  HeadlessSpotifyOAuth ..> SpotifyAuthRequired : raises
  spotify_api *-- HeadlessSpotifyOAuth : singleton
  spotify_api ..> Track : returns

  app_factory ..> routes : registers bp
  app_factory ..> power_state : wake() at boot
  routes ..> spotify_api
  routes ..> power_state
  routes ..> volume
  routes ..> encoder
  routes ..> motor_control
  routes ..> SpotifyAuthRequired : catches → 503
  authorize ..> spotify_api : get_auth_manager()

  spotify_api ..> volume : set_current_volume() on poll
  volume ..> spotify_api : «callback» set_device_volume
  volume ..> encoder : get_position() every 20ms
  volume ..> motor_control : step() / stop() / spin()
```

The `volume` ↔ `spotify_api` cycle is intentional. `volume` doesn't import
Spotify code. When `spotify_api` is imported, it registers `set_device_volume`
as a listener, and volume calls that from its monitor thread.

## 3. Frontend components

```mermaid
classDiagram
  direction TB

  class main["main.jsx"] {
    <<entry>>
    createRoot().render()
  }

  class SpinProvider {
    <<context>>
    +spinSeconds
    +setSpinSeconds()
    fetch /presets.json
  }

  class NowPlayingProvider {
    <<context>>
    +nowPlaying: Track
    +prevTrack: Track
    +nextTrack: Track
    +awake: bool
    +wake()
    +sleepDisplay()
    +applyNowPlaying(track)
    +applyPreloadedTrack(dir) bool
    +reconcileNowPlaying(track)
    -fetchNowPlaying() every 5s
    -fetchStatus() every 2s asleep
    -preloadCover(src)
  }

  class App {
    <<component>>
    -optimisticPlaying
    -handleNext()
    -handlePrevious()
    -handleTogglePlay(shouldPlay)
    renders black stage when asleep
  }

  class CurrentTrackCover {
    <<component>>
  }
  class WarpedCover {
    <<component>>
    +src
    +size
  }
  class CurrentTrackInfo {
    <<component>>
    +progressMs
    +phase
    +dragX
    marquee + fade on track change
  }
  class VolumeArc {
    <<component>>
    fetch /api/volume every 200ms
    visible 2s after a change
  }

  class useSwipeGesture {
    <<hook>>
    +SWIPE_THRESHOLD_PX = 180
    touch path + pointer path
    returns dragX, phase, handlers
  }
  class useSimulatedProgress {
    <<hook>>
    formatTime.js
  }
  class playbackActions {
    <<module>>
    200ms shared cooldown
    +playNext()
    +playPrevious()
    +pausePlayback()
    +resumePlayback()
    +wakeDisplay()
    +sleepDisplay()
  }
  class warpToCircle {
    <<module>>
    +warpSquareToCircle(image, canvas, size, strength)
  }

  class API["Flask /api"] {
    <<external>>
  }

  main *-- SpinProvider
  SpinProvider *-- NowPlayingProvider
  NowPlayingProvider *-- App
  App *-- CurrentTrackCover
  App *-- CurrentTrackInfo
  App *-- VolumeArc
  CurrentTrackCover *-- WarpedCover

  App ..> NowPlayingProvider : useNowPlaying()
  App ..> useSwipeGesture
  App ..> useSimulatedProgress
  App ..> playbackActions
  CurrentTrackCover ..> NowPlayingProvider : useNowPlaying()
  CurrentTrackCover ..> SpinProvider : useSpin()
  CurrentTrackInfo ..> NowPlayingProvider : useNowPlaying()
  WarpedCover ..> warpToCircle
  NowPlayingProvider ..> playbackActions : wake/sleep

  NowPlayingProvider ..> API : GET now_playing, status
  playbackActions ..> API : POST actions
  VolumeArc ..> API : GET volume
```

## 4. Sequence: swipe to next track

```mermaid
sequenceDiagram
  autonumber
  actor User
  participant Swipe as useSwipeGesture
  participant App
  participant Ctx as NowPlayingProvider
  participant Act as playbackActions
  participant Routes as routes.py
  participant SA as spotify_api
  participant Spotify as Spotify Web API

  Note over Ctx: Every 5s: GET /api/now_playing →<br/>{prev, now_playing, next}, preload prev/next covers

  User->>Swipe: touch drag left ≥ 180px
  Swipe->>App: onNext()
  App->>Ctx: applyPreloadedTrack('next')
  Ctx-->>App: true (UI shows next track now, progress 0)
  App->>Act: playNext()
  Act->>Routes: POST /api/next_track
  Routes->>SA: next_track()
  SA->>Spotify: next_track()
  loop up to 5 × 150ms
    SA->>Spotify: current_playback()
    Note right of SA: stop once the track name changes
  end
  SA-->>Routes: Track
  Routes-->>Act: {success, now_playing}
  Act-->>App: data
  App->>Ctx: reconcileNowPlaying(data.now_playing)
  Ctx->>Routes: GET /api/now_playing (refresh prev/next)
```

## 5. Sequence: wheel as volume knob

```mermaid
sequenceDiagram
  actor User
  participant Enc as encoder (gpiozero thread)
  participant Vol as volume monitor thread
  participant Motor as motor_control
  participant SA as spotify_api.set_device_volume
  participant Spotify as Spotify Web API
  participant Arc as VolumeArc (200ms poll)

  Note over Vol: Starts lazily on first get/set_current_volume
  Motor->>Motor: spin() and the wheel turns under motor power
  User->>Enc: grabs wheel and holds it still
  loop every 20ms
    Vol->>Enc: get_position()
  end
  Note over Vol: still within deadzone for ≥ 0.5s → halted
  Vol->>Motor: stop()
  Note over Vol: armed

  User->>Enc: turns wheel
  Vol->>Enc: get_position() → delta°
  Note over Vol: volume += delta / 0.9° (90° arc = 0–100)
  loop per whole unit crossed
    Vol->>Motor: step(direction), opposing 40ms pulse ("click")
  end
  Vol->>SA: listener(new_volume)
  SA->>Spotify: volume(n)
  Arc->>Vol: GET /api/volume → get_current_volume()
  Note over Arc: arc shown for 2s

  Note over Vol: no movement for 2s
  Vol->>Motor: spin()
  Note over Vol: disarmed, so motor rotation is ignored
```
