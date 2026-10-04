from __future__ import annotations

import json

import psycopg

from enterprise_context.config import get_settings
from enterprise_context.migrations import apply_migrations


def main() -> None:
    settings = get_settings()
    with psycopg.connect(settings.database_url) as connection, connection.transaction():
        applied = apply_migrations(connection, settings.migrations_dir)
    print(json.dumps({"applied_migrations": applied}))


if __name__ == "__main__":
    main()
