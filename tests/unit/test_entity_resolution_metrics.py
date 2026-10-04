from datetime import datetime, timezone

from enterprise_context.domain.models import (
    GoldenEntityPair,
    SupplierSourceRecord,
)
from enterprise_context.entity_resolution.evaluation import evaluate_entity_resolution
from enterprise_context.entity_resolution.resolver import resolve_supplier_records


def test_review_candidates_are_reported_separately_from_automatic_merges() -> None:
    observed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    records = [
        SupplierSourceRecord(
            source_system="ERP",
            source_record_id="A-1",
            observed_at=observed_at,
            supplier_name="Acme Corp",
            country_code="US",
        ),
        SupplierSourceRecord(
            source_system="MASTER",
            source_record_id="A-2",
            observed_at=observed_at,
            supplier_name="Acme Corporation",
            country_code="US",
        ),
        SupplierSourceRecord(
            source_system="ERP",
            source_record_id="B-1",
            observed_at=observed_at,
            supplier_name="Globex LLC",
            country_code="US",
            tax_id="TAX-1",
        ),
        SupplierSourceRecord(
            source_system="MASTER",
            source_record_id="B-2",
            observed_at=observed_at,
            supplier_name="Globex LLC",
            country_code="US",
            tax_id="TAX-2",
        ),
        SupplierSourceRecord(
            source_system="ERP",
            source_record_id="C-1",
            observed_at=observed_at,
            supplier_name="Northstar Industrial Incorporated",
            country_code="US",
        ),
        SupplierSourceRecord(
            source_system="MASTER",
            source_record_id="C-2",
            observed_at=observed_at,
            supplier_name="Northstar Industrail Incorporated",
            country_code="US",
        ),
    ]
    pairs = [
        GoldenEntityPair(
            left_source_record_id="A-1", right_source_record_id="A-2", same_entity=True
        ),
        GoldenEntityPair(
            left_source_record_id="B-1", right_source_record_id="B-2", same_entity=False
        ),
        GoldenEntityPair(
            left_source_record_id="C-1", right_source_record_id="C-2", same_entity=True
        ),
    ]

    metrics = evaluate_entity_resolution(pairs, resolve_supplier_records(records))

    assert metrics["auto_merge_precision"] == 1.0
    assert metrics["auto_merge_recall"] == 0.5
    assert metrics["candidate_recall"] == 1.0
    assert metrics["review_candidates"] == 1
    assert metrics["false_merge_rate"] == 0.0
