from __future__ import annotations

import json
from pathlib import Path

import psycopg

from enterprise_context.config import get_settings
from enterprise_context.ingestion.postgres import ingest_generated_data

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with psycopg.connect(get_settings().database_url) as connection:
        result = ingest_generated_data(
            connection,
            ROOT / "data",
            ROOT / "infra" / "sql",
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
