import builtins
import re

import pytest

from backend import create_app, spotify_api


@pytest.fixture
def no_token(tmp_path, monkeypatch):
    """Point Spotipy at an empty token cache, as on a fresh/stale install."""
    monkeypatch.setenv("SPOTIPY_CACHE_PATH", str(tmp_path / ".cache"))
    monkeypatch.setattr(spotify_api, "_auth_manager", None)
    monkeypatch.setattr(spotify_api, "_spotify_client", None)
    # Anything that tries to prompt for the OAuth redirect URL would hang a
    # headless service forever; fail loudly here instead of blocking the suite.
    monkeypatch.setattr(builtins, "input", lambda *a, **kw: pytest.fail("prompted for input"))


@pytest.fixture
def app():
    app = create_app({"TESTING": True})
    # set up here
    yield app
    # tear down here


@pytest.fixture
def client(app):
    return app.test_client()


def test_index_serves_frontend_bundle(client):
    # `/` is the kiosk's entry point: frontend/dist/index.html, plus the
    # hashed JS bundle it references. If either 404s, Cog shows a blank page.
    response = client.get("/")
    assert response.status_code == 200
    assert b'<div id="root">' in response.data

    bundle = re.search(rb'<script[^>]+src="(/assets/[^"]+\.js)"', response.data)
    assert bundle, "index.html doesn't reference a JS bundle"
    assert client.get(bundle.group(1).decode()).status_code == 200


def test_now_playing_without_token_returns_503(client, no_token):
    response = client.get("/api/now_playing")
    assert response.status_code == 503
    assert response.json["error"] == "spotify_auth_required"
    assert response.json["auth_url"].startswith("https://accounts.spotify.com/authorize")


def test_playback_action_without_token_returns_503(client, no_token):
    response = client.post("/api/next_track")
    assert response.status_code == 503
    assert response.json["error"] == "spotify_auth_required"


def test_auth_status_without_token(client, no_token):
    response = client.get("/api/auth_status")
    assert response.status_code == 200
    assert response.json == {"authorized": False}


def test_get_spotify_client_never_prompts(no_token):
    with pytest.raises(spotify_api.SpotifyAuthRequired):
        spotify_api.get_spotify_client()


def test_auth_manager_refuses_to_prompt(no_token):
    # Even reached from inside Spotipy (a cache that disappears between the
    # up-front check and the request), the auth flow must raise, not prompt.
    with pytest.raises(spotify_api.SpotifyAuthRequired):
        spotify_api.get_auth_manager().get_access_token(as_dict=False, check_cache=False)
