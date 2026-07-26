import os

from flask import Blueprint, jsonify, render_template, send_from_directory
from flask import current_app as app

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


@bp.route("/current_cover")
def covers():
    current_cover = "redveilAlbumCover.jpg"
    return jsonify({"cover": f"/api/cover/{current_cover}"})
