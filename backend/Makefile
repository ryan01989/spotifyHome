.PHONY: all clean venv run format lint mypy test dist

all: run

clean:
	rm -rf .venv build dist .pytest_cache *.egg-info

.venv:
	# --system-site-packages: on the Pi, the motor and encoder drive GPIO
	# through apt's python3-lgpio, which pip can't reliably build.
	python3 -m venv --system-site-packages .venv && \
		.venv/bin/pip install --upgrade pip setuptools && \
		.venv/bin/pip install --editable ".[dev]"

venv: .venv

run: venv
	.venv/bin/flask --app backend --debug run --port 5001

test: venv
	.venv/bin/pytest

dist: venv test
	.venv/bin/pip wheel --wheel-dir dist --no-deps .
