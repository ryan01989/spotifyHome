# SpotifyWheel Backend

SpotifyWheel Backend description

## Quick Start

Run the application:

    make run

And open it in the browser at [http://localhost:5000/](http://localhost:5000/)

## Prerequisites

Python >=3.10

## Development environment

 - `make venv`: creates a virtualenv with dependencies and this application
   installed in [development mode](http://setuptools.readthedocs.io/en/latest/setuptools.html#development-mode)

 - `make run`: runs a development server in debug mode (changes in source code
   are reloaded automatically)

 - `make test`: runs tests (see also: [Testing Flask Applications](https://flask.palletsprojects.com/en/3.0.x/testing/))

 - `make dist`: creates a wheel distribution (will run tests first)

 - `make clean`: removes virtualenv and build artifacts

 - add application dependencies in `pyproject.toml` under `project.dependencies`;
   add development dependencies under `project.optional-dependencies.*`; run
   `make clean && make venv` to reinstall the environment

## Configuration

Default configuration is loaded from `backend.defaults` and can be
overriden by environment variables with a `FLASK_` prefix. See
[Configuring from Environment Variables](https://flask.palletsprojects.com/en/3.0.x/config/#configuring-from-environment-variables).

Consider using
[dotenv](https://flask.palletsprojects.com/en/3.0.x/cli/#environment-variables-from-dotenv).

## Deployment

See [Deploying to Production](https://flask.palletsprojects.com/en/3.0.x/deploying/).

You may use the distribution (`make dist`) to publish it to a package index,
deliver to your server, or copy in your `Dockerfile`, and insall it with `pip`.

You must set a
[SECRET_KEY](https://flask.palletsprojects.com/en/3.0.x/tutorial/deploy/#configure-the-secret-key)
in production to a secret and stable value.
