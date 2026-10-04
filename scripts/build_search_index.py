from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from enterprise_context.retrieval.dependencies import get_search_retriever
from enterprise_context.retrieval.models import IndexedDocument

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def main() -> None:
    documents = [
        IndexedDocument.model_validate(record)
        for record in read_jsonl(ROOT / "data" / "raw" / "generated" / "documents.jsonl")
    ]
    retriever = get_search_retriever()
    retriever.ensure_index()
    indexed = retriever.index_documents(documents)
    print(json.dumps({"index": "enterprise-context-documents-v1", "indexed_documents": indexed}))


if __name__ == "__main__":
    main()
