.PHONY: all clean venv run format lint mypy test dist

all: run

clean:
	rm -rf .venv build dist .pytest_cache *.egg-info

.venv:
	python3 -m venv .venv && \
		.venv/bin/pip install --upgrade pip setuptools && \
		.venv/bin/pip install --editable ".[dev]"

venv: .venv

run: venv
	.venv/bin/flask --app backend --debug run --port 5001

test: venv
	.venv/bin/pytest

dist: venv test
	.venv/bin/pip wheel --wheel-dir dist --no-deps .
