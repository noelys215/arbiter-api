.PHONY: dev api backfill-tmdb-details

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(or $(shell command -v python3 2>/dev/null),$(shell command -v python 2>/dev/null)))
BACKFILL_ARGS ?= --dry-run

dev: api

api:
	@if [ -z "$(PYTHON)" ]; then echo "No Python interpreter found (tried .venv/bin/python, python3, python)." >&2; exit 1; fi
	$(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

backfill-tmdb-details:
	@if [ -z "$(PYTHON)" ]; then echo "No Python interpreter found (tried .venv/bin/python, python3, python)." >&2; exit 1; fi
	$(PYTHON) scripts/backfill_tmdb_title_details.py $(BACKFILL_ARGS)
