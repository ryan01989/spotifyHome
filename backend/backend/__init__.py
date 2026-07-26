from flask import Flask

from backend import routes
from backend.logging import init_logging


def create_app(config_overrides=None):
    init_logging()  # should be configured before any access to app.logger

    app = Flask(__name__)
    app.config.from_object("backend.defaults")
    app.config.from_prefixed_env()

    if config_overrides is not None:
        app.config.from_mapping(config_overrides)

    app.register_blueprint(routes.bp)

    return app
