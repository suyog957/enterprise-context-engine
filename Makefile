.PHONY: web-install web-build api-test lint typecheck test data-generate data-ingest resolve-entities build-graph build-search-index evaluate-smoke

web-install:
	cd apps/web && npm install

web-build:
	cd apps/web && npm run build

api-test:
	python -m pytest

lint:
	ruff check .

typecheck:
	mypy src apps/api

test: api-test

# Implemented in the synthetic ingestion phase.
data-generate:
	python scripts/generate_synthetic_data.py

resolve-entities:
	python scripts/resolve_entities.py

data-ingest:
	python scripts/ingest_data.py

build-graph:
	python scripts/build_graph.py --upload

build-search-index:
	python scripts/build_search_index.py

evaluate-smoke:
	python scripts/run_evaluation.py --smoke --fail-on-regression
