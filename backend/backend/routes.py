import os

from flask import Blueprint, jsonify, render_template, send_from_directory
from flask import current_app as app
from backend.spotify_api import get_current_track, get_playing, pause_playback, resume_playback, next_track, previous_track

bp = Blueprint("api", __name__, url_prefix="/api")

PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")


@bp.route("/")
def index():
    app.logger.warning("sample message")
    return render_template("index.html")


# Current Plan for serving album covers:
# Serve __THREE__ albums covers at once, The past, current, and next song covers.
# This should allow the user to see the current song, as well as swipe up and see
# past and future. Also will make transitions seemless. The frontend will hit this
# endpoint on ever


@bp.route("/cover/<path:filename>")
def cover_file(filename):
    return send_from_directory(PUBLIC_DIR, filename)


@bp.route("/current_track")
def current_track():
    current_track = get_current_track()
    return jsonify(current_track)


@bp.route("/now_playing")
def now_playing():
    now_playing = get_playing()
    # app.logger.info(f"Now playing: {now_playing}")
    return jsonify(now_playing)


def _action_response(now_playing):
    if now_playing is None:
        return jsonify({"success": False}), 502
    return jsonify({"success": True, "now_playing": now_playing})


@bp.route("/pause_playback", methods=["POST"])
def pause_playback_route():
    return _action_response(pause_playback())


@bp.route("/resume_playback", methods=["POST"])
def resume_playback_route():
    return _action_response(resume_playback())


@bp.route("/next_track", methods=["POST"])
def next_track_route():
    return _action_response(next_track())


@bp.route("/previous_track", methods=["POST"])
def previous_track_route():
    return _action_response(previous_track())
