from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from backend import routes
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

    app.register_blueprint(routes.bp)

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    return app
