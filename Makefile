PYTHON ?= python3.14

.PHONY: install run format lint test

install:
	$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) -m app.main

format:
	$(PYTHON) -m ruff check --fix app tests
	$(PYTHON) -m ruff format app tests

lint:
	$(PYTHON) -m ruff check app tests
	$(PYTHON) -m mypy app

test:
	$(PYTHON) -m pytest

