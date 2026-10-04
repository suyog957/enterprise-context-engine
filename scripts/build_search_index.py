from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg

from enterprise_context.config import get_settings
from enterprise_context.retrieval.dependencies import get_search_retriever
from enterprise_context.retrieval.models import IndexedDocument
from enterprise_context.retrieval.projection import publish_search_version

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def main() -> None:
    documents = [
        IndexedDocument.model_validate(record)
        for record in read_jsonl(ROOT / "data" / "raw" / "generated" / "documents.jsonl")
    ]
    with psycopg.connect(get_settings().database_url, autocommit=True) as connection:
        result = publish_search_version(get_search_retriever(), connection, documents)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
