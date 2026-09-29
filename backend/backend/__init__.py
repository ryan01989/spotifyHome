from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from backend import power_state, routes
from backend.logging import init_logging

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def create_app(config_overrides=None):
    load_dotenv()
    init_logging()

    app = Flask(__name__, static_folder=str(FRONTEND_DIST), static_url_path="")
    app.config.from_object("backend.defaults")
    app.config.from_prefixed_env()

    if config_overrides is not None:
        app.config.from_mapping(config_overrides)

    # Start awake by default — plugging the Pi in (backend boots via
    # kiosk.service) should turn the display on, not leave it waiting for a
    # wake trigger it hasn't received yet.
    power_state.wake()

    app.register_blueprint(routes.bp)

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    return app
