from datetime import datetime, timezone

from enterprise_context.domain.models import (
    EntityResolutionResult,
    ResolutionDecision,
    ResolutionMethod,
)
from enterprise_context.ingestion.postgres import (
    build_canonical_supplier_rows,
    build_erp_supplier_index,
    build_purchase_order_row,
    build_source_supplier_rows,
)


def test_review_candidate_is_not_promoted_to_transactional_supplier_index() -> None:
    source_records = [
        {
            "source_system": "ERP",
            "source_supplier_id": "SUP-0000",
            "source_record_id": "ERP-SUP-0000",
        }
    ]
    resolutions = {
        "ERP-SUP-0000": EntityResolutionResult(
            canonical_entity_id="candidate-acme",
            source_system="ERP",
            source_record_id="ERP-SUP-0000",
            alias="Acme Corp",
            normalized_name="acme corp",
            resolution_method=ResolutionMethod.FUZZY_NAME,
            confidence_score=0.9,
            decision=ResolutionDecision.REVIEW,
            candidate_entity_id="supplier-acme",
            decided_at=datetime.now(timezone.utc),
        )
    }

    assert build_erp_supplier_index(source_records, resolutions) == {}


def test_review_candidate_has_no_canonical_foreign_key_until_reviewed() -> None:
    source_records = [
        {
            "source_system": "SUPPLIER_MASTER",
            "source_supplier_id": "VEND-1",
            "source_record_id": "MASTER-1",
        }
    ]
    resolutions = {
        "MASTER-1": EntityResolutionResult(
            canonical_entity_id="review-candidate",
            source_system="SUPPLIER_MASTER",
            source_record_id="MASTER-1",
            alias="Acme Corpp",
            normalized_name="acme corpp",
            resolution_method=ResolutionMethod.FUZZY_NAME,
            confidence_score=0.9,
            decision=ResolutionDecision.REVIEW,
            candidate_entity_id="supplier-acme",
            decided_at=datetime.now(timezone.utc),
        )
    }

    row = build_source_supplier_rows(source_records, resolutions)[0]

    assert row[2] is None
    assert row[6] is True


def test_canonical_supplier_rows_join_source_supplier_ids_to_resolution_ids() -> None:
    suppliers = [
        {
            "supplier_id": "SUP-0000",
            "supplier_name": "Acme Corp",
            "country_code": "US",
            "postal_code": "10000",
            "status": "ACTIVE",
            "risk_rating": "LOW",
            "approved_categories": ["Software"],
            "source_system": "SUPPLIER_MASTER",
        }
    ]

    rows = build_canonical_supplier_rows(
        suppliers, {"SUP-0000": "supplier-canonical-acme"}
    )

    assert rows["supplier-canonical-acme"] == (
        "supplier-canonical-acme",
        "Acme Corp",
        "US",
        "10000",
        "ACTIVE",
        "LOW",
        ["Software"],
        "SUPPLIER_MASTER",
    )


def test_purchase_order_mapping_retains_unresolved_supplier_and_missing_amount() -> None:
    row = {
        "purchase_order_id": "PO-5001",
        "requisition_id": "PR-1001",
        "supplier_id": "UNKNOWN-5001",
        "buyer_id": "BUY-001",
        "amount": None,
        "currency": "USD",
        "status": "OPEN",
        "source_system": "ERP",
    }

    mapped = build_purchase_order_row(row, {})

    assert mapped[2] == "UNKNOWN-5001"
    assert mapped[3] is None
    assert mapped[5] is None
    assert mapped[8].obj == ["UNRESOLVED_SUPPLIER"]


def test_purchase_order_mapping_keeps_valid_canonical_supplier() -> None:
    row = {
        "purchase_order_id": "PO-5002",
        "requisition_id": "PR-1002",
        "supplier_id": "SUP-0002",
        "buyer_id": "BUY-002",
        "amount": "1234.50",
        "currency": "USD",
        "status": "OPEN",
        "source_system": "ERP",
    }

    mapped = build_purchase_order_row(row, {"SUP-0002": "supplier-canonical-2"})

    assert mapped[3] == "supplier-canonical-2"
    assert str(mapped[5]) == "1234.50"
    assert mapped[8].obj == []
