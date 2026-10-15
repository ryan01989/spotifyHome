from pathlib import Path

from dotenv import load_dotenv

# Before the imports below: motor_control, encoder and volume read their
# tuning env vars (MOTOR_SPIN_SPEED_PERCENT, ...) at import time, so loading
# .env only in create_app() left those settings silently ignored.
load_dotenv()

from flask import Flask  # noqa: E402

from backend import power_state, routes  # noqa: E402
from backend.logging import init_logging
from backend.physical_control import calibration

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

    # Applies the saved wheel calibration and raises the startup prompt; the
    # motor stays off until the prompt is done, skipped or times out.
    calibration.load()

    app.register_blueprint(routes.bp)

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    return app
