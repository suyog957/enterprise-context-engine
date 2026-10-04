"""Outbox projector: applies committed PostgreSQL changes to the RDF projection.

Transactions write their state change and an outbox event atomically. This worker
claims due events with ``FOR UPDATE SKIP LOCKED`` (safe with several workers), applies
each to the current graph version, and records it in a per-graph ledger so retries
and rebuild replays are idempotent. Failures back off exponentially and end in
DEAD_LETTER after ``max_attempts``; nothing is silently dropped.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from rdflib import XSD, Literal

from enterprise_context.config import get_settings
from enterprise_context.graph.projection import GRAPH_PROJECTION, current_projection
from enterprise_context.graph.query import FusekiGraphStore, GraphQueryError
from enterprise_context.graph.templates import PREFIXES, resource_uri
from enterprise_context.observability.logging import configure_logging
from enterprise_context.observability.tracing import configure_tracing, traced

logger = logging.getLogger(__name__)
GRAPH_PUBLISH_LOCK = 724002  # same key publish_graph_version holds exclusively


class UnsupportedEventError(ValueError):
    """Raised for event types the projector does not know; these are dead-lettered."""


@dataclass(frozen=True)
class OutboxEvent:
    event_id: UUID
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: Mapping[str, Any]
    attempts: int
    created_at: datetime


@dataclass
class WorkerReport:
    claimed: int = 0
    published: int = 0
    skipped_already_applied: int = 0
    failed: int = 0
    dead_lettered: int = 0
    errors: list[str] = field(default_factory=list)


def _uri(kind: str, identifier: str) -> str:
    return f"<{resource_uri(kind, identifier)}>"


def purchase_order_created_update(graph_uri: str, event: OutboxEvent) -> str:
    """Idempotent SPARQL update: re-applying it yields the same graph."""
    payload = event.payload
    po_id = str(payload["purchase_order_id"])
    po = _uri("purchase-order", po_id)
    requisition = _uri("requisition", str(payload["requisition_id"]))
    source = _uri("source-record", f"CONTEXT_PLATFORM:{po_id}")
    facts = [
        f"{po} a ecg:PurchaseOrder, ecg:BusinessDocument, ecg:Entity",
        f"{po} ecg:canonicalId {Literal(po_id).n3()}",
        f"{po} ecg:displayName {Literal(po_id).n3()}",
        f"{po} ecg:createdFrom {requisition}",
        f"{po} ecg:displayStatus {Literal('OPEN').n3()}",
        f"{po} ecg:currency {Literal(str(payload['currency'])).n3()}",
        f"{po} ecg:amount {Literal(str(payload['amount']), datatype=XSD.decimal).n3()}",
        f"{po} ecg:createdAt {Literal(event.created_at.isoformat(), datatype=XSD.dateTime).n3()}",
        f"{po} prov:wasDerivedFrom {source}",
        f"{source} a ecg:SourceRecord, prov:Entity",
        f"{source} ecg:hasSourceSystem {Literal('CONTEXT_PLATFORM').n3()}",
        f"{source} ecg:hasSourceRecordId {Literal(po_id).n3()}",
        f"{requisition} ecg:hasState ecg:CONVERTED",
    ]
    if payload.get("canonical_supplier_id"):
        facts.append(
            f"{po} ecg:hasSupplier {_uri('supplier', str(payload['canonical_supplier_id']))}"
        )
    if payload.get("buyer_id"):
        facts.append(f"{po} ecg:ownedByBuyer {_uri('buyer', str(payload['buyer_id']))}")
    graph = f"<{graph_uri}>"
    inserts = " .\n    ".join(facts)
    return f"""{PREFIXES}
DELETE {{ GRAPH {graph} {{ {requisition} ecg:hasState ?state }} }}
WHERE {{ GRAPH {graph} {{ {requisition} ecg:hasState ?state }} }} ;
INSERT DATA {{ GRAPH {graph} {{
    {inserts} .
}} }}"""


UpdateBuilder = Callable[[str, OutboxEvent], str]
GRAPH_HANDLERS: dict[str, UpdateBuilder] = {
    "PURCHASE_ORDER_CREATED": purchase_order_created_update,
}


class GraphProjector:
    def __init__(self, store: FusekiGraphStore) -> None:
        self._store = store

    def apply(self, event: OutboxEvent, graph_uri: str) -> None:
        builder = GRAPH_HANDLERS.get(event.event_type)
        if builder is None:
            raise UnsupportedEventError(f"No graph handler for {event.event_type}")
        with traced(
            "projection.apply", **{"ecg.event_type": event.event_type, "ecg.graph_uri": graph_uri}
        ):
            self._store.sparql_update(builder(graph_uri, event))


def _event(row: Mapping[str, Any]) -> OutboxEvent:
    return OutboxEvent(
        event_id=row["event_id"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        event_type=row["event_type"],
        payload=row["payload"],
        attempts=int(row["attempts"]),
        created_at=row["created_at"],
    )


def apply_to_graph(
    connection: psycopg.Connection[Any],
    projector: GraphProjector,
    event: OutboxEvent,
    graph_uri: str,
) -> bool:
    """Apply one event to one graph version unless the ledger says it already was."""
    already = connection.execute(
        """SELECT 1 FROM projection_applied_event
           WHERE projection = %s AND target_uri = %s AND event_id = %s""",
        (GRAPH_PROJECTION, graph_uri, event.event_id),
    ).fetchone()
    if already:
        return False
    projector.apply(event, graph_uri)
    connection.execute(
        """INSERT INTO projection_applied_event (projection, target_uri, event_id)
           VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
        (GRAPH_PROJECTION, graph_uri, event.event_id),
    )
    return True


def replay_events(
    connection: psycopg.Connection[Any], projector: GraphProjector, graph_uri: str
) -> tuple[int, datetime | None]:
    """Replay every supported committed event, oldest first, onto a (new) graph version.

    Returns (events newly applied, newest event time) for the source watermark.
    """
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """SELECT event_id, aggregate_type, aggregate_id, event_type, payload, attempts,
                      created_at
               FROM projection_outbox
               WHERE event_type = ANY(%s) AND status <> 'DEAD_LETTER'
               ORDER BY created_at, event_id""",
            (list(GRAPH_HANDLERS),),
        ).fetchall()
    applied = sum(apply_to_graph(connection, projector, _event(row), graph_uri) for row in rows)
    return applied, (rows[-1]["created_at"] if rows else None)


class ProjectionWorker:
    def __init__(
        self,
        database_url: str,
        projector: GraphProjector,
        *,
        batch_size: int = 20,
        max_attempts: int = 5,
        base_backoff_seconds: float = 2.0,
        claim_timeout_seconds: int = 300,
    ) -> None:
        self._database_url = database_url
        self._projector = projector
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._base_backoff_seconds = base_backoff_seconds
        self._claim_timeout_seconds = claim_timeout_seconds

    def backoff_seconds(self, attempts: int) -> float:
        return float(min(self._base_backoff_seconds * 2 ** max(attempts - 1, 0), 300.0))

    def _claim(self, connection: psycopg.Connection[Any]) -> list[OutboxEvent]:
        with connection.transaction(), connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(
                """SELECT event_id, aggregate_type, aggregate_id, event_type, payload,
                          attempts, created_at
                   FROM projection_outbox
                   WHERE (status IN ('PENDING', 'FAILED') AND next_attempt_at <= now())
                      OR (status = 'PROCESSING'
                          AND claimed_at < now() - make_interval(secs => %s))
                   ORDER BY created_at, event_id
                   LIMIT %s
                   FOR UPDATE SKIP LOCKED""",
                (self._claim_timeout_seconds, self._batch_size),
            ).fetchall()
            for row in rows:
                cursor.execute(
                    """UPDATE projection_outbox
                       SET status = 'PROCESSING', claimed_at = now(), attempts = attempts + 1
                       WHERE event_id = %s""",
                    (row["event_id"],),
                )
        return [_event({**row, "attempts": int(row["attempts"]) + 1}) for row in rows]

    def process_batch(self) -> WorkerReport:
        report = WorkerReport()
        with psycopg.connect(self._database_url, connect_timeout=3, autocommit=True) as connection:
            events = self._claim(connection)
            report.claimed = len(events)
            for event in events:
                self._process(connection, event, report)
        return report

    def _process(
        self, connection: psycopg.Connection[Any], event: OutboxEvent, report: WorkerReport
    ) -> None:
        try:
            with connection.transaction():
                # Shared lock: wait for an in-progress graph publish to switch versions.
                connection.execute("SELECT pg_advisory_xact_lock_shared(%s)", (GRAPH_PUBLISH_LOCK,))
                projection = current_projection(connection)
                if projection is None:
                    raise GraphQueryError("No graph projection is published yet")
                applied = apply_to_graph(connection, self._projector, event, projection.target_uri)
                connection.execute(
                    """UPDATE projection_outbox
                       SET status = 'PUBLISHED', published_at = now(), last_error = NULL
                       WHERE event_id = %s""",
                    (event.event_id,),
                )
                connection.execute(
                    """UPDATE projection_state
                       SET source_watermark = GREATEST(
                             COALESCE(source_watermark, '-infinity'::timestamptz), %s)
                       WHERE projection = %s""",
                    (event.created_at, GRAPH_PROJECTION),
                )
            if applied:
                report.published += 1
            else:
                report.skipped_already_applied += 1
        except UnsupportedEventError as error:
            self._fail(connection, event, str(error), dead_letter=True)
            report.dead_lettered += 1
            report.errors.append(str(error))
        except (GraphQueryError, psycopg.Error) as error:
            dead = event.attempts >= self._max_attempts
            self._fail(connection, event, str(error), dead_letter=dead)
            if dead:
                report.dead_lettered += 1
            else:
                report.failed += 1
            report.errors.append(str(error))

    def _fail(
        self,
        connection: psycopg.Connection[Any],
        event: OutboxEvent,
        message: str,
        *,
        dead_letter: bool,
    ) -> None:
        logger.warning(
            "Projection event failed",
            extra={
                "event_id": str(event.event_id),
                "attempts": event.attempts,
                "dead": dead_letter,
            },
        )
        with connection.transaction():
            connection.execute(
                """UPDATE projection_outbox
                   SET status = %s, last_error = %s,
                       next_attempt_at = now() + make_interval(secs => %s)
                   WHERE event_id = %s""",
                (
                    "DEAD_LETTER" if dead_letter else "FAILED",
                    message[:1000],
                    self.backoff_seconds(event.attempts),
                    event.event_id,
                ),
            )

    def run_forever(self, stop: threading.Event, *, poll_seconds: float = 2.0) -> None:
        while not stop.is_set():
            try:
                report = self.process_batch()
            except psycopg.Error as error:
                logger.warning("Projection worker cannot reach PostgreSQL: %s", error)
                stop.wait(poll_seconds * 5)
                continue
            if report.claimed:
                logger.info("Projection batch processed", extra=report.__dict__)
            if report.claimed < self._batch_size:
                stop.wait(poll_seconds)


def build_worker() -> ProjectionWorker:
    settings = get_settings()
    store = FusekiGraphStore(
        settings.fuseki_url,
        admin_user=settings.fuseki_admin_user,
        admin_password=settings.fuseki_admin_password,
    )
    return ProjectionWorker(settings.database_url, GraphProjector(store))


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply projection outbox events.")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(
        service_name="enterprise-context-projector",
        service_version=settings.service_version,
        environment=settings.environment,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
    )
    worker = build_worker()
    if args.once:
        print(worker.process_batch().__dict__)
        return
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logger.info("Projection worker started")
    worker.run_forever(stop)


if __name__ == "__main__":
    main()
