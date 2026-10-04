"""Forward-only SQL migrations tracked in the ``schema_migration`` table.

Files are named ``NNN_description.sql``; each is applied once, in numeric order,
inside the caller's transaction.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import tuple_row

_MIGRATION_FILE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def discover_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    migrations: list[tuple[int, Path]] = []
    for path in migrations_dir.glob("*.sql"):
        match = _MIGRATION_FILE.match(path.name)
        if match:
            migrations.append((int(match.group(1)), path))
    versions = [version for version, _ in migrations]
    if len(versions) != len(set(versions)):
        raise ValueError(f"Duplicate migration versions in {migrations_dir}")
    return sorted(migrations)


def apply_migrations(connection: psycopg.Connection[Any], migrations_dir: Path) -> list[int]:
    """Apply pending migrations and return the versions applied by this call."""
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS schema_migration (
                   version INTEGER PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        # Serialize concurrent migrators (API start-up and scripts) on one advisory lock.
        cursor.execute("SELECT pg_advisory_xact_lock(724001)")
        cursor.execute("SELECT version FROM schema_migration")
        applied = {int(row[0]) for row in cursor.fetchall()}
        newly_applied: list[int] = []
        for version, path in discover_migrations(migrations_dir):
            if version in applied:
                continue
            cursor.execute(path.read_text(encoding="utf-8"))
            cursor.execute("INSERT INTO schema_migration (version) VALUES (%s)", (version,))
            newly_applied.append(version)
    return newly_applied
