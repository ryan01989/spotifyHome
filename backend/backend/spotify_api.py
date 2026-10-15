import logging
import os
import time
from pathlib import Path

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.exceptions import SpotifyOauthError

from backend.physical_control import volume as volume_control

log = logging.getLogger(__name__)

SCOPE = (
    "user-read-playback-state user-modify-playback-state user-read-playback-position"
    " user-read-currently-playing user-read-recently-played"
)

# A bare CacheFileHandler() resolves .cache against the *current working
# directory*, so the token cache silently moves depending on where the app was
# launched from — start it from the wrong directory and it looks like a fresh,
# never-authorized install. Anchor it to backend/ (this file's package parent)
# instead, which is where the cache has always lived.
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache"

_spotify_client = None
_auth_manager = None

# The active device's id and whether it accepts volume commands, from the last
# playback poll. Phones typically report supports_volume=False (and a fixed
# volume_percent of 100) and 403 any volume change.
_device_id = None
_device_supports_volume = True


class SpotifyAuthRequired(Exception):
    """Raised when there's no usable cached token to talk to Spotify with.

    Recovering means running the OAuth flow, which needs a human with a
    browser — so it happens out-of-band (`python -m backend.authorize`), never
    inside a request.
    """

    def __init__(self, authorize_url=None):
        super().__init__(
            "Spotify authorization required — run `python -m backend.authorize` "
            "from the backend/ directory."
        )
        self.authorize_url = authorize_url


class _HeadlessSpotifyOAuth(spotipy.SpotifyOAuth):
    # SpotifyOAuth's fallback, whenever it can't satisfy a token request from
    # cache, is to *prompt*: open a browser and block on input() waiting for
    # the pasted redirect URL. On the Pi the app runs under systemd with no
    # browser and no tty, so that turns a stale/missing .cache into a request
    # handler hanging (or dying on EOF) rather than failing usefully. Refuse
    # to prompt at all; callers get SpotifyAuthRequired and the app stays up.
    def get_auth_response(self, open_browser=None):
        raise SpotifyAuthRequired(self.get_authorize_url())


def get_cache_path():
    return os.getenv("SPOTIPY_CACHE_PATH") or str(DEFAULT_CACHE_PATH)


def get_auth_manager():
    # Reuse one auth manager instead of constructing a fresh SpotifyOAuth on
    # every call — each instantiation re-reads the token cache file
    # independently, and with the dev server's threaded polling that opens a
    # window for overlapping requests to race on token refresh.
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = _HeadlessSpotifyOAuth(
            client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
            scope=SCOPE,
            open_browser=False,
            cache_handler=CacheFileHandler(cache_path=get_cache_path()),
        )
    return _auth_manager


def get_cached_token(auth_manager=None):
    """Return a valid token dict from the cache, or None — never prompts.

    Refreshes on the spot if the cached access token has expired (that's a
    plain HTTP call, no user interaction). A refresh that fails — revoked
    token, rotated client credentials — counts as "no token".
    """
    auth_manager = auth_manager or get_auth_manager()
    try:
        return auth_manager.validate_token(auth_manager.cache_handler.get_cached_token())
    except SpotifyOauthError as e:
        log.warning(f"Cached Spotify token could not be refreshed: {e}")
        return None


def is_authorized():
    return get_cached_token() is not None


def get_spotify_client():
    global _spotify_client
    auth_manager = get_auth_manager()
    if _spotify_client is None:
        _spotify_client = spotipy.Spotify(auth_manager=auth_manager)
    # Check the cache up front so a missing/unusable token surfaces as
    # SpotifyAuthRequired here, rather than deep inside spotipy's request path.
    if get_cached_token(auth_manager) is None:
        raise SpotifyAuthRequired(auth_manager.get_authorize_url())
    return _spotify_client


def _sync_volume_from_device(playback):
    # Keeps volume_control's tracked volume in step with the actual device
    # (e.g. changed from a phone or the Spotify web player) every time we
    # poll for the current track. current_playback (unlike currently_playing)
    # includes the device's volume_percent for exactly this reason.
    global _device_id, _device_supports_volume
    if not playback:
        return
    device = playback.get("device") or {}
    supports_volume = device.get("supports_volume", True)
    if device.get("id") != _device_id or supports_volume != _device_supports_volume:
        if not supports_volume:
            log.warning(
                f"Spotify device '{device.get('name')}' doesn't allow volume control "
                "through the Web API (phones usually don't) - the wheel can't set its volume"
            )
        _device_id = device.get("id")
        _device_supports_volume = supports_volume

    volume_percent = device.get("volume_percent")
    if volume_percent is not None:
        volume_control.set_current_volume(volume_percent)


def get_current_track():
    sp = get_spotify_client()
    try:
        current_track = sp.current_playback(additional_types="episode")
        _sync_volume_from_device(current_track)
        # Spin the wheel while music plays. No playback at all (no active
        # device) counts as paused.
        volume_control.set_playing(bool(current_track and current_track.get("is_playing")))
        return current_track
    except spotipy.exceptions.SpotifyException as e:
        log.warning(f"Error fetching current track: {e}")
        return None


def get_last_track():
    sp = get_spotify_client()
    try:
        last_track = sp.current_user_recently_played(limit=1)
        return last_track
    except spotipy.exceptions.SpotifyException as e:
        log.warning(f"Error fetching last track: {e}")
        return None


def get_next_track():
    sp = get_spotify_client()
    try:
        next_track = sp.queue()
        return next_track
    except spotipy.exceptions.SpotifyException as e:
        log.warning(f"Error fetching next track: {e}")
        return None


def get_playing(trackType="current"):
    track = None
    if trackType == "current":
        track = get_current_track()
    elif trackType == "last":
        track = get_last_track()
    elif trackType == "next":
        track = get_next_track()
    else:
        log.warning(f"Invalid trackType: {trackType}")
        return None

    if track is None:
        return None

    # Each Spotify endpoint nests the track/episode under a different key:
    # currently_playing() uses "item", recently_played() uses "items"[0]["track"],
    # and queue() uses "queue"[0].
    if trackType == "current":
        item = track.get("item")
    elif trackType == "last":
        items = track.get("items") or []
        item = items[0]["track"] if items else None
    else:
        queue = track.get("queue") or []
        item = queue[0] if queue else None

    if item is None:
        return None

    # Tracks nest artwork under `album`; podcast episodes have no `album`
    # and carry their own artwork directly on `images` instead.
    images = item.get("album", {}).get("images") or item.get("images")
    cover = images[0]["url"] if images else None

    # Tracks have an `artists` list; episodes have a `show` instead.
    if "artists" in item:
        artist = ", ".join(a["name"] for a in item["artists"])
    else:
        artist = item.get("show", {}).get("name")

    return {
        "cover": cover,
        "name": item.get("name"),
        "artist": artist,
        "progress_ms": track.get("progress_ms"),
        "duration_ms": item.get("duration_ms"),
        "is_playing": track.get("is_playing"),
    }


def pause_playback():
    sp = get_spotify_client()
    try:
        sp.pause_playback()
        # Spotify's state can lag the command, so the re-read may still say
        # playing; set the motor from what we just did, after that re-read.
        now_playing = get_playing()
        volume_control.set_playing(False)
        return now_playing
    except spotipy.exceptions.SpotifyException as e:
        log.warning(f"Error pausing playback: {e}")
        return None


def resume_playback():
    sp = get_spotify_client()
    try:
        sp.start_playback()
        now_playing = get_playing()  # see pause_playback()
        volume_control.set_playing(True)
        return now_playing
    except spotipy.exceptions.SpotifyException as e:
        log.warning(f"Error resuming playback: {e}")
        return None


def _get_now_playing_after_change(previous_name):
    # sp.next_track()/previous_track() acknowledge immediately, but Spotify's
    # own currently-playing state can take a moment to catch up — querying it
    # right away can still report the track we just skipped away from. Poll
    # briefly until it reports something different (or give up and return
    # whatever we last saw).
    now_playing = None
    for _ in range(5):
        time.sleep(0.15)
        now_playing = get_playing()
        if now_playing and now_playing.get("name") != previous_name:
            break
    return now_playing


def next_track():
    sp = get_spotify_client()
    try:
        previous_name = (get_playing() or {}).get("name")
        sp.next_track()
        return _get_now_playing_after_change(previous_name)
    except spotipy.exceptions.SpotifyException as e:
        log.warning(f"Error skipping to next track: {e}")
        return None


def previous_track():
    sp = get_spotify_client()
    try:
        previous_name = (get_playing() or {}).get("name")
        sp.previous_track()
        return _get_now_playing_after_change(previous_name)
    except spotipy.exceptions.SpotifyException as e:
        log.warning(f"Error going back to previous track: {e}")
        return None


def set_device_volume(volume_percent):
    # Called from volume.py's monitor thread, not a request — an exception
    # escaping here kills that thread for good, so SpotifyAuthRequired has to
    # be swallowed rather than propagated the way it is out of the routes.
    if not _device_supports_volume:
        return  # already warned in _sync_volume_from_device; it would just 403
    try:
        sp = get_spotify_client()
        sp.volume(int(volume_percent))
    except SpotifyAuthRequired as e:
        log.warning(f"Cannot set device volume: {e}")
    except spotipy.exceptions.SpotifyException as e:
        log.warning(f"Error setting device volume: {e}")


# Wire the wheel's volume knob to the actual device - volume_control.py
# knows nothing about Spotify, it just calls this back on every unit change.
volume_control.set_volume_change_listener(set_device_volume)
