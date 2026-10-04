"""Projector, replay and rebuild against live PostgreSQL and an isolated Fuseki dataset."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from enterprise_context.graph.projection import current_projection, publish_graph_version
from enterprise_context.graph.query import FusekiGraphStore
from enterprise_context.migrations import apply_migrations
from enterprise_context.projection.worker import (
    GraphProjector,
    ProjectionWorker,
    replay_events,
)

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = "https://example.org/enterprise-context/"
ECG = "https://example.org/enterprise-context#"
BASE = (
    f"<{RESOURCE}requisition/PR-9001> <{ECG}hasState> <{ECG}APPROVED> .\n"
    f'<{RESOURCE}requisition/PR-9001> <{ECG}canonicalId> "PR-9001" .\n'
).encode()

pytestmark = pytest.mark.integration


@pytest.fixture
def env() -> dict[str, str]:
    values = {
        "database": os.environ.get("TEST_DATABASE_URL", ""),
        "fuseki": os.environ.get("TEST_FUSEKI_URL", ""),
        "password": os.environ.get("FUSEKI_ADMIN_PASSWORD", ""),
    }
    if not all(values.values()):
        pytest.skip("TEST_DATABASE_URL, TEST_FUSEKI_URL and FUSEKI_ADMIN_PASSWORD are required")
    with psycopg.connect(values["database"], autocommit=True) as connection:
        with connection.transaction():
            apply_migrations(connection, ROOT / "infra" / "sql")
        connection.execute(
            "TRUNCATE projection_applied_event, projection_outbox, projection_state, "
            "projection_history"
        )
    return values


def writer(env: dict[str, str], url: str | None = None) -> FusekiGraphStore:
    return FusekiGraphStore(
        url or env["fuseki"], admin_user="admin", admin_password=env["password"]
    )


def reader(env: dict[str, str], graph_uri: str) -> FusekiGraphStore:
    return FusekiGraphStore(env["fuseki"], graph_uri_provider=lambda: graph_uri)


def add_event(
    connection: psycopg.Connection[Any], event_type: str = "PURCHASE_ORDER_CREATED"
) -> str:
    event_id = uuid4()
    connection.execute(
        """INSERT INTO projection_outbox (event_id, aggregate_type, aggregate_id, event_type,
                                          payload)
           VALUES (%s, 'PurchaseOrder', 'PO-T1', %s, %s)""",
        (
            event_id,
            event_type,
            Jsonb(
                {
                    "purchase_order_id": "PO-T1",
                    "requisition_id": "PR-9001",
                    "canonical_supplier_id": "supplier-x",
                    "buyer_id": "BUY-000",
                    "amount": "10.00",
                    "currency": "USD",
                }
            ),
        ),
    )
    return str(event_id)


def status_of(connection: psycopg.Connection[Any], event_id: str) -> tuple[Any, ...]:
    row = connection.execute(
        "SELECT status, attempts, next_attempt_at > now() "
        "FROM projection_outbox WHERE event_id = %s",
        (event_id,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def converted(env: dict[str, str], graph_uri: str) -> bool:
    result = reader(env, graph_uri).run_readonly_sparql(
        f"ASK {{ <{RESOURCE}requisition/PR-9001> <{ECG}hasState> <{ECG}CONVERTED> . "
        f"<{RESOURCE}purchase-order/PO-T1> <{ECG}createdFrom> ?r }}"
    )
    return bool(result.boolean)


def test_projector_applies_events_idempotently_and_rebuild_matches(env: dict[str, str]) -> None:
    store = writer(env)
    projector = GraphProjector(store)
    with psycopg.connect(env["database"], autocommit=True) as connection:
        base = publish_graph_version(
            store, connection, BASE, expected_triples=2, content_hash="base"
        )
        event_id = add_event(connection)

        worker = ProjectionWorker(env["database"], projector)
        first = worker.process_batch()
        assert (first.claimed, first.published) == (1, 1)
        assert status_of(connection, event_id)[0] == "PUBLISHED"
        assert converted(env, base.target_uri)
        incremental_count = store.count_triples(base.target_uri)

        # A redelivered event is detected by the ledger and does not change the graph.
        connection.execute(
            "UPDATE projection_outbox SET status = 'PENDING', next_attempt_at = now()"
        )
        again = worker.process_batch()
        assert again.skipped_already_applied == 1
        assert store.count_triples(base.target_uri) == incremental_count

        # Rebuild from source data + replay yields the same graph as incremental updates.
        def replay(graph_uri: str) -> datetime | None:
            return replay_events(connection, projector, graph_uri)[1]

        rebuilt = publish_graph_version(
            store,
            connection,
            BASE,
            expected_triples=2,
            content_hash="base",
            before_switch=replay,
        )
        state = current_projection(connection)

    assert rebuilt.version == base.version + 1
    assert rebuilt.item_count == incremental_count
    assert converted(env, rebuilt.target_uri)
    assert state is not None and state.source_watermark is not None


def test_failures_back_off_then_dead_letter(env: dict[str, str]) -> None:
    store = writer(env)
    with psycopg.connect(env["database"], autocommit=True) as connection:
        publish_graph_version(store, connection, BASE, expected_triples=2, content_hash="base")
        failing_id = add_event(connection)
        unknown_id = add_event(connection, "SUPPLIER_RENAMED")

        broken = GraphProjector(writer(env, "http://fuseki:3030/does-not-exist"))
        worker = ProjectionWorker(env["database"], broken, max_attempts=2)
        report = worker.process_batch()
        assert (report.failed, report.dead_lettered) == (1, 1)
        assert status_of(connection, failing_id) == ("FAILED", 1, True)
        assert status_of(connection, unknown_id)[0] == "DEAD_LETTER"

        connection.execute(
            "UPDATE projection_outbox SET next_attempt_at = now() WHERE event_id = %s",
            (failing_id,),
        )
        worker.process_batch()
        assert status_of(connection, failing_id)[0] == "DEAD_LETTER"
