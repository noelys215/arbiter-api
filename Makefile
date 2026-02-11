.PHONY: dev api

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(or $(shell command -v python3 2>/dev/null),$(shell command -v python 2>/dev/null)))

dev: api

api:
	@if [ -z "$(PYTHON)" ]; then echo "No Python interpreter found (tried .venv/bin/python, python3, python)." >&2; exit 1; fi
	$(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
