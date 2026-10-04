"""Versioned publication of the RDF context-graph projection.

Each build is written to a new named graph ``urn:ecg:graph:context:v{N}``, verified,
and then made current by a single PostgreSQL transaction on ``projection_state``.
Readers resolve the current graph URI from PostgreSQL, so a switch is atomic from
their point of view; the previous graph is dropped afterwards.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from enterprise_context.graph.query import FusekiGraphStore, GraphQueryError
from enterprise_context.projection.state import lock_and_next_version, record_published_version

GRAPH_PROJECTION = "context_graph"
GRAPH_URI_PREFIX = "urn:ecg:graph:context:v"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectionVersion:
    projection: str
    version: int
    target_uri: str
    content_hash: str | None
    item_count: int | None
    published_at: datetime
    source_watermark: datetime | None = None


def graph_uri_for(version: int) -> str:
    return f"{GRAPH_URI_PREFIX}{version}"


def current_projection(
    connection: psycopg.Connection[Any], projection: str = GRAPH_PROJECTION
) -> ProjectionVersion | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """SELECT projection, version, target_uri, content_hash, item_count, published_at,
                      source_watermark
               FROM projection_state WHERE projection = %s AND status = 'PUBLISHED'""",
            (projection,),
        ).fetchone()
    return ProjectionVersion(**row) if row else None


class CurrentGraphResolver:
    """Resolves the current graph URI from PostgreSQL with a short TTL cache."""

    def __init__(self, database_url: str, *, ttl_seconds: float = 5.0) -> None:
        self._database_url = database_url
        self._ttl_seconds = ttl_seconds
        self._cached: tuple[float, ProjectionVersion | None] | None = None

    def current(self) -> ProjectionVersion | None:
        now = time.monotonic()
        if self._cached is not None and now - self._cached[0] < self._ttl_seconds:
            return self._cached[1]
        try:
            with psycopg.connect(self._database_url, connect_timeout=3) as connection:
                version = current_projection(connection)
        except psycopg.Error as error:
            raise GraphQueryError("Graph projection state is unavailable") from error
        self._cached = (now, version)
        return version

    def __call__(self) -> str | None:
        version = self.current()
        return version.target_uri if version else None


def publish_graph_version(
    store: FusekiGraphStore,
    connection: psycopg.Connection[Any],
    ntriples: bytes,
    *,
    expected_triples: int,
    content_hash: str,
    before_switch: Callable[[str], datetime | None] | None = None,
) -> ProjectionVersion:
    """Publish a validated graph as the next version and retire the previous one.

    ``before_switch`` runs against the new graph while the publish lock is held (the
    projector waits on the same lock), e.g. to replay committed outbox events; it may
    return the newest event time it applied, recorded as the source watermark.

    The version switch must be committed before the previous graph is dropped, so the
    caller must pass a connection without an open transaction (ideally autocommit);
    otherwise ``transaction()`` would only create a savepoint.
    """
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("publish_graph_version requires a connection with no open transaction")
    with connection.transaction():
        next_version = lock_and_next_version(connection, GRAPH_PROJECTION)
        previous = current_projection(connection)
        graph_uri = graph_uri_for(next_version)

        store.put_named_graph(graph_uri, ntriples)
        published_count = store.count_triples(graph_uri)
        if published_count != expected_triples:
            store.drop_named_graph(graph_uri)
            raise GraphQueryError(
                f"Published graph has {published_count} triples, expected {expected_triples}"
            )
        watermark = before_switch(graph_uri) if before_switch is not None else None
        if before_switch is not None:
            published_count = store.count_triples(graph_uri)
        record_published_version(
            connection,
            GRAPH_PROJECTION,
            next_version,
            graph_uri,
            content_hash=content_hash,
            item_count=published_count,
        )
        if watermark is not None:
            connection.execute(
                "UPDATE projection_state SET source_watermark = %s WHERE projection = %s",
                (watermark, GRAPH_PROJECTION),
            )

    # Readers have switched; old graphs (including orphans from failed runs) can go.
    stale = set(store.list_named_graphs(GRAPH_URI_PREFIX)) - {graph_uri}
    if previous is not None:
        stale.add(previous.target_uri)
    for stale_uri in sorted(stale):
        try:
            store.drop_named_graph(stale_uri)
        except GraphQueryError:
            logger.warning("Could not drop retired graph %s; it will be retried", stale_uri)

    current = current_projection(connection)
    if current is None:  # pragma: no cover - guarded by the transaction above
        raise GraphQueryError("Graph projection state was not recorded")
    return current
