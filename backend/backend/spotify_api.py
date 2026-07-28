import os
import time
from tokenize import String

import spotipy
from spotipy.cache_handler import CacheFileHandler

_spotify_client = None


def get_spotify_client():
    # Reuse one client/auth-manager instead of constructing a fresh
    # SpotifyOAuth on every call — each instantiation re-reads the token
    # cache file independently, and with the dev server's threaded polling
    # that opens a window for overlapping requests to race on token refresh.
    global _spotify_client
    if _spotify_client is None:
        _spotify_client = spotipy.Spotify(
            auth_manager=spotipy.SpotifyOAuth(
                client_id=os.getenv("SPOTIFY_CLIENT_ID"),
                client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
                redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
                scope="user-read-playback-state user-modify-playback-state user-read-playback-position" +
                " user-read-currently-playing user-read-recently-played",
                cache_handler=CacheFileHandler(),
            )
        )
    return _spotify_client


def get_current_track():
    sp = get_spotify_client()
    try:
        current_track = sp.currently_playing(additional_types="episode")
        return current_track
    except spotipy.exceptions.SpotifyException as e:
        print(f"Error fetching current track: {e}")
        return None


def get_last_track():
    sp = get_spotify_client()
    try:
        last_track = sp.current_user_recently_played(limit=1)
        return last_track
    except spotipy.exceptions.SpotifyException as e:
        print(f"Error fetching last track: {e}")
        return None


def get_next_track():
    sp = get_spotify_client()
    try:
        next_track = sp.queue()
        return next_track
    except spotipy.exceptions.SpotifyException as e:
        print(f"Error fetching next track: {e}")
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
        print(f"Invalid trackType: {trackType}")
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
        return get_playing()
    except spotipy.exceptions.SpotifyException as e:
        print(f"Error pausing playback: {e}")
        return None


def resume_playback():
    sp = get_spotify_client()
    try:
        sp.start_playback()
        return get_playing()
    except spotipy.exceptions.SpotifyException as e:
        print(f"Error resuming playback: {e}")
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
        print(f"Error skipping to next track: {e}")
        return None


def previous_track():
    sp = get_spotify_client()
    try:
        previous_name = (get_playing() or {}).get("name")
        sp.previous_track()
        return _get_now_playing_after_change(previous_name)
    except spotipy.exceptions.SpotifyException as e:
        print(f"Error going back to previous track: {e}")
        return None
