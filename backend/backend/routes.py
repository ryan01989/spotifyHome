from flask import Blueprint, jsonify, render_template, request
from flask import current_app as app
from backend.spotify_api import (
    SpotifyAuthRequired,
    get_current_track,
    get_playing,
    is_authorized,
    next_track,
    pause_playback,
    previous_track,
    resume_playback,
)
from backend.physical_control.encoder import get_position, get_velocity, get_acceleration
from backend.physical_control import motor_control, volume as volume_control
from backend import power_state

bp = Blueprint("api", __name__, url_prefix="/api")


# The OAuth flow needs a browser and a human, of whcich we initially have neither,
# so spotify_api refuses to start it from inside a request
# and raises instead. Turn 503 the frontend can read (and a log
# line pointing at the fix) rather than a 500 or a hung handler.
@bp.errorhandler(SpotifyAuthRequired)
def handle_spotify_auth_required(error):
    app.logger.warning(
        "Spotify authorization required — no usable token cache. Run "
        "`.venv/bin/python -m backend.authorize` from backend/ to (re)authorize."
    )
    return jsonify({
        "error": "spotify_auth_required",
        "message": str(error),
        "auth_url": error.authorize_url,
    }), 503


@bp.route("/")
def index():
    app.logger.warning("sample message")
    return render_template("index.html")



@bp.route("/current_track")
def current_track():
    current_track = get_current_track()
    return jsonify(current_track)


@bp.route("/now_playing")
def now_playing():
    prev = get_playing("last")
    now_playing = get_playing()
    next = get_playing("next")
    return jsonify({"prev": prev, "now_playing": now_playing, "next": next})


@bp.route("/status")
def status():
    return jsonify({"awake": power_state.is_awake()})


@bp.route("/auth_status")
def auth_status():
    # Cheap, never-prompting check — reads (and refreshes) the token cache
    # only. Lets the kiosk tell "Spotify isn't authorized" apart from
    # "nothing is playing", both of which otherwise look like empty data.
    return jsonify({"authorized": is_authorized()})


@bp.route("/volume")
def volume():
    return jsonify({"volume": volume_control.get_current_volume()})


@bp.route("/wake", methods=["POST"])
def wake():
    return jsonify({"awake": power_state.wake()})


@bp.route("/sleep", methods=["POST"])
def sleep():
    return jsonify({"awake": power_state.sleep()})


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


@bp.route("/wheel_position")
def wheel_position():
    return jsonify({"position": get_position()})


@bp.route("/wheel_velocity")
def wheel_velocity():
    return jsonify({"velocity": get_velocity()})


@bp.route("/wheel_acceleration")
def wheel_acceleration():
    return jsonify({"acceleration": get_acceleration()})


@bp.route("/wheel_spin", methods=["POST"])
def wheel_spin():
    data = request.get_json(silent=True) or {}
    motor_control.spin(data.get("speed_percent"), data.get("direction", motor_control.DIRECTION_CW))
    return jsonify({"success": True})


@bp.route("/wheel_stop", methods=["POST"])
def wheel_stop():
    motor_control.stop()
    return jsonify({"success": True})


@bp.route("/wheel_stop_immediately", methods=["POST"])
def wheel_stop_immediately():
    motor_control.stop_immediately()
    return jsonify({"success": True})


@bp.route("/wheel_step", methods=["POST"])
def wheel_step():
    data = request.get_json(silent=True) or {}
    direction = data.get("direction")
    if direction not in (motor_control.DIRECTION_CW, motor_control.DIRECTION_CCW):
        return jsonify({"success": False, "error": "direction must be 1 (CW) or -1 (CCW)"}), 400
    motor_control.step(direction)
    return jsonify({"success": True})
