# All Python targets run inside the Compose `tools` container by default, so the
# only host requirement is Docker. Override with `make PY=python <target>` to use a
# local Python 3.12 virtual environment instead.
TOOLS ?= docker compose --profile tools run --rm tools
PY ?= $(TOOLS) python

.PHONY: up down tools-build web-install web-build lint typecheck test test-integration \
	migrate data-generate resolve-entities data-ingest build-graph build-search-index \
	pipeline evaluate-smoke evaluate check rebuild-projections process-outbox

up:
	docker compose up -d --build

down:
	docker compose down

tools-build:
	docker compose --profile tools build tools

web-install:
	cd apps/web && npm install

web-build:
	cd apps/web && npm run build

lint:
	$(PY) -m ruff check src apps/api scripts tests

typecheck:
	$(PY) -m mypy src apps/api scripts tests

test:
	$(PY) -m pytest

test-integration:
	$(PY) -m pytest -m integration

check: lint typecheck test

migrate:
	$(PY) scripts/migrate.py

data-generate:
	$(PY) scripts/generate_synthetic_data.py

resolve-entities:
	$(PY) scripts/resolve_entities.py

data-ingest:
	$(PY) scripts/ingest_data.py

build-graph:
	$(PY) scripts/build_graph.py --upload

build-search-index:
	$(PY) scripts/build_search_index.py

rebuild-projections:
	$(PY) scripts/rebuild_projections.py --graph --search

process-outbox:
	$(PY) scripts/rebuild_projections.py --process-outbox

# Full local data pipeline against the running Compose stack.
pipeline: migrate data-generate resolve-entities data-ingest build-graph build-search-index evaluate-smoke

evaluate-smoke:
	$(PY) scripts/run_evaluation.py --smoke --fail-on-regression

evaluate:
	$(PY) scripts/run_evaluation.py --fail-on-regression --policy-backend opa
