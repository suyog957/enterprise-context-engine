from datetime import UTC, datetime
from uuid import uuid4

import pytest
from rdflib import Dataset, URIRef
from rdflib.plugins.sparql.parser import parseUpdate

from enterprise_context.projection.worker import (
    GraphProjector,
    OutboxEvent,
    ProjectionWorker,
    UnsupportedEventError,
    purchase_order_created_update,
)

GRAPH = "urn:ecg:graph:context:v3"
RESOURCE = "https://example.org/enterprise-context/"
ECG = "https://example.org/enterprise-context#"


def event(event_type: str = "PURCHASE_ORDER_CREATED") -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid4(),
        aggregate_type="PurchaseOrder",
        aggregate_id="PO-ABC",
        event_type=event_type,
        payload={
            "purchase_order_id": "PO-ABC",
            "requisition_id": "PR-1012",
            "canonical_supplier_id": "supplier-x",
            "buyer_id": "BUY-000",
            "amount": "18000.00",
            "currency": "CAD",
        },
        attempts=1,
        created_at=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
    )


def test_purchase_order_update_is_valid_sparql_and_idempotent() -> None:
    update = purchase_order_created_update(GRAPH, event())
    parseUpdate(update)

    dataset = Dataset()
    named = dataset.graph(URIRef(GRAPH))
    named.add(
        (
            URIRef(f"{RESOURCE}requisition/PR-1012"),
            URIRef(f"{ECG}hasState"),
            URIRef(f"{ECG}APPROVED"),
        )
    )
    dataset.update(update)
    first = sorted(named)
    dataset.update(update)

    assert sorted(named) == first
    states = list(named.objects(URIRef(f"{RESOURCE}requisition/PR-1012"), URIRef(f"{ECG}hasState")))
    assert states == [URIRef(f"{ECG}CONVERTED")]
    assert (URIRef(f"{RESOURCE}purchase-order/PO-ABC"), None, None) in named


def test_unknown_event_types_are_rejected_for_dead_lettering() -> None:
    class Store:
        def sparql_update(self, update: str) -> None:
            raise AssertionError("must not be called")

    with pytest.raises(UnsupportedEventError):
        GraphProjector(Store()).apply(event("SUPPLIER_RENAMED"), GRAPH)  # type: ignore[arg-type]


def test_backoff_grows_exponentially_and_is_capped() -> None:
    worker = ProjectionWorker("postgresql://unused", GraphProjector(None))  # type: ignore[arg-type]

    assert [worker.backoff_seconds(n) for n in (1, 2, 3, 4)] == [2.0, 4.0, 8.0, 16.0]
    assert worker.backoff_seconds(20) == 300.0
