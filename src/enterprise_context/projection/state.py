"""Version bookkeeping shared by every projection (graph, search index)."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import tuple_row

_LOCK_IDS = {"context_graph": 724002, "search_index": 724003}


def lock_and_next_version(connection: psycopg.Connection[Any], projection: str) -> int:
    """Serialize publishers of one projection and return its next version number.

    Must be called inside a transaction; the advisory lock is held until it ends.
    """
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_IDS.get(projection, 724099),))
        row = cursor.execute(
            "SELECT COALESCE(MAX(version), 0) FROM projection_history WHERE projection = %s",
            (projection,),
        ).fetchone()
    return int(row[0] if row else 0) + 1


def record_published_version(
    connection: psycopg.Connection[Any],
    projection: str,
    version: int,
    target_uri: str,
    *,
    content_hash: str | None,
    item_count: int,
) -> None:
    """Make ``target_uri`` current and retire earlier versions (inside a transaction)."""
    connection.execute(
        """INSERT INTO projection_state
           (projection, version, target_uri, content_hash, item_count, status)
           VALUES (%s, %s, %s, %s, %s, 'PUBLISHED')
           ON CONFLICT (projection) DO UPDATE SET
             version = EXCLUDED.version, target_uri = EXCLUDED.target_uri,
             content_hash = EXCLUDED.content_hash, item_count = EXCLUDED.item_count,
             status = 'PUBLISHED', published_at = now(), source_watermark = NULL""",
        (projection, version, target_uri, content_hash, item_count),
    )
    connection.execute(
        """INSERT INTO projection_history
           (projection, version, target_uri, content_hash, item_count)
           VALUES (%s, %s, %s, %s, %s)""",
        (projection, version, target_uri, content_hash, item_count),
    )
    connection.execute(
        """UPDATE projection_history SET retired_at = now()
           WHERE projection = %s AND version < %s AND retired_at IS NULL""",
        (projection, version),
    )
