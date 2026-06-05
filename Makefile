.PHONY: install lint test build api worker frontend preview-example

PYTHON ?= .venv/bin/python
PYTHON_BOOTSTRAP ?= python3.11
PIP ?= $(PYTHON) -m pip
NPM ?= npm

install:
	$(PYTHON_BOOTSTRAP) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	cd frontend && $(NPM) ci

lint:
	$(PYTHON) -m compileall backend cli
	$(PYTHON) -m ruff check .

test:
	$(PYTHON) -m pytest

build:
	cd frontend && $(NPM) run build

api:
	$(PYTHON) -m uvicorn model_eval_api.main:app --reload --app-dir backend

worker:
	$(PYTHON) -m rq worker model-eval --url "$${REDIS_URL:-redis://localhost:6379/0}"

frontend:
	cd frontend && $(NPM) run dev

preview-example:
	$(PYTHON) -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml
