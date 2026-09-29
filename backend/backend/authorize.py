"""One-off Spotify OAuth flow — the only place that's allowed to prompt.

    cd backend && .venv/bin/python -m backend.authorize [--force]

The app itself never runs the flow: on the Pi it's a systemd service with no
browser and no tty, so prompting there would hang a request handler instead of
authorizing anything (see _HeadlessSpotifyOAuth in spotify_api.py). Run this
by hand instead — over SSH is fine, it prints a URL you open on any machine
and paste the redirect back — and it writes the token cache the service reads.
"""

import sys

from dotenv import load_dotenv
from spotipy.exceptions import SpotifyOauthError

from backend import spotify_api

# SpotifyOAuth stores each credential under its own attribute name; the map is
# only used to report which .env entry is missing.
_ATTR_FOR_ENV = {
    "SPOTIFY_CLIENT_ID": "client_id",
    "SPOTIFY_CLIENT_SECRET": "client_secret",
    "SPOTIPY_REDIRECT_URI": "redirect_uri",
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    force = "--force" in argv

    load_dotenv()

    auth = spotify_api.get_auth_manager()
    missing = [name for name, attr in _ATTR_FOR_ENV.items() if not getattr(auth, attr)]
    if missing:
        print(f"Missing from backend/.env: {', '.join(missing)}")
        return 1

    cache_path = spotify_api.get_cache_path()

    if not force and spotify_api.get_cached_token(auth) is not None:
        print(f"Already authorized — token cache at {cache_path}.")
        print("Re-run with --force to authorize again.")
        return 0

    print("1. Open this URL and approve access:\n")
    print(f"   {auth.get_authorize_url()}\n")
    print("2. Your browser lands on the redirect URI with ?code=... appended.")
    print("   It may fail to load — that's fine, only the URL matters.\n")

    try:
        response = input("Paste the full redirect URL here: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1

    if not response:
        print("Nothing pasted; aborted.")
        return 1

    try:
        # Returns the input unchanged when it has no ?code=, so pasting a bare
        # code works too; a ?error= in the URL raises SpotifyOauthError.
        code = auth.parse_response_code(response)
        auth.get_access_token(code=code, as_dict=False, check_cache=False)
    except SpotifyOauthError as e:
        print(f"Authorization failed: {e}")
        return 1

    print(f"\nAuthorized. Token cache written to {cache_path}.")
    print("Restart the backend (sudo systemctl restart spotifywheel) to pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
