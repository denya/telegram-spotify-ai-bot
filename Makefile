PYENV := $(shell command -v pyenv 2>/dev/null)
PYTHON_VERSION ?= $(strip $(shell [ -f .python-version ] && cat .python-version))

ifeq ($(strip $(PYTHON_VERSION)),)
PYTHON_VERSION := 3.14-dev
endif

DEFAULT_PYTHON_BIN := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")

ifeq ($(PYENV),)
PYTHON ?= $(DEFAULT_PYTHON_BIN)
else
PYTHON ?= PYENV_VERSION=$(PYTHON_VERSION) pyenv exec python
endif

ifeq ($(strip $(PYTHON)),)
$(error Could not determine a Python interpreter. Install Python or configure pyenv.)
endif

.PHONY: install run run-combined fmt format lint test python-info pyenv-bootstrap run-bot run-web dev

python-info:
	@printf "Python command: %s\n" "$(PYTHON)"
	@if $(PYTHON) -c 'import sys' >/dev/null 2>&1; then \
		$(PYTHON) -c 'import sys; print("Python version:", sys.version.split()[0]); print("Executable:", sys.executable)'; \
	else \
		printf "Unable to execute Python via '%s'. Try 'make pyenv-bootstrap'.\n" "$(PYTHON)"; \
	fi
	@if [ -n "$(PYENV)" ]; then \
		if pyenv version-name >/dev/null 2>&1; then \
			printf "pyenv active version: %s\n" "$$(pyenv version-name)"; \
		else \
			printf "pyenv expected version (not yet installed): %s\n" "$(PYTHON_VERSION)"; \
		fi; \
	else \
		printf "pyenv not detected; using system python.\n"; \
	fi

pyenv-bootstrap:
	@if [ -z "$(PYENV)" ]; then \
		printf "pyenv not installed. Install pyenv first: https://github.com/pyenv/pyenv#installation\n"; \
		exit 1; \
	fi
	@set -e; \
	printf "Ensuring Python %s is available via pyenv...\n" "$(PYTHON_VERSION)"; \
	pyenv install -s "$(PYTHON_VERSION)"; \
	printf "Setting local pyenv version to %s...\n" "$(PYTHON_VERSION)"; \
	pyenv local "$(PYTHON_VERSION)"; \
	printf "pyenv local version configured. Reactivate your shell or use 'pyenv exec'.\n"

install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install -r requirements-dev.txt

run:
	$(PYTHON) -m app.main

run-combined:
	$(PYTHON) -m app.main --combined

run-bot:
	$(PYTHON) -m app.main

run-web:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

dev:
	@echo "Starting web (http://127.0.0.1:8000) and Telegram bot. Press Ctrl+C to stop." \
	&& set -e; \
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload & \
	WEB_PID=$$!; \
	STATUS=0; \
	$(PYTHON) -m app.main || STATUS=$$?; \
	kill $$WEB_PID 2>/dev/null || true; \
	wait $$WEB_PID 2>/dev/null || true; \
	exit $$STATUS

fmt: format

format:
	$(PYTHON) -m ruff check --fix app tests
	$(PYTHON) -m ruff format app tests

lint:
	$(PYTHON) -m ruff check app tests
	$(PYTHON) -m mypy app

test:
	$(PYTHON) -m pytest

