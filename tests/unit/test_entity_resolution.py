from datetime import datetime, timezone

from enterprise_context.domain.models import (
    ResolutionDecision,
    SupplierSourceRecord,
)
from enterprise_context.entity_resolution.normalization import normalize_supplier_name
from enterprise_context.entity_resolution.resolver import resolve_supplier_records


def supplier_record(
    source_record_id: str,
    name: str,
    *,
    source_system: str = "ERP",
    tax_id: str | None = None,
    domain: str | None = None,
    country: str = "US",
) -> SupplierSourceRecord:
    return SupplierSourceRecord(
        source_system=source_system,
        source_record_id=source_record_id,
        observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        supplier_name=name,
        country_code=country,
        tax_id=tax_id,
        website_domain=domain,
    )


def test_normalization_handles_casing_punctuation_and_corporate_suffixes() -> None:
    assert normalize_supplier_name("ACME Corporation, Inc.") == "acme corp inc"


def test_exact_normalized_names_auto_merge_without_identifiers() -> None:
    records = [
        supplier_record("ERP-1", "Acme Corp"),
        supplier_record("MASTER-1", "ACME Corporation", source_system="SUPPLIER_MASTER"),
    ]

    results = resolve_supplier_records(records)

    assert len(results) == 2
    assert results[0].canonical_entity_id == results[1].canonical_entity_id
    assert results[1].decision is ResolutionDecision.AUTO_MERGE


def test_fuzzy_candidate_is_sent_to_review_not_auto_merged() -> None:
    records = [
        supplier_record("ERP-1", "Northstar Industrial Incorporated"),
        supplier_record(
            "MASTER-1",
            "Northstar Industrail Incorporated",
            source_system="SUPPLIER_MASTER",
        ),
    ]

    results = resolve_supplier_records(records)

    assert results[1].decision is ResolutionDecision.REVIEW
    assert results[1].candidate_entity_id == results[0].canonical_entity_id
    assert results[1].canonical_entity_id != results[0].canonical_entity_id


def test_conflicting_tax_ids_prevent_name_based_merge() -> None:
    records = [
        supplier_record("ERP-1", "Acme Corp", tax_id="TAX-1"),
        supplier_record("MASTER-1", "Acme Corporation", tax_id="TAX-2"),
    ]

    results = resolve_supplier_records(records)

    assert results[1].decision is ResolutionDecision.SEPARATE
    assert results[1].canonical_entity_id != results[0].canonical_entity_id


def test_identifier_conflict_cannot_be_bridged_by_an_identifierless_record() -> None:
    records = [
        supplier_record("A-1", "Example Vendor LLC", tax_id="TAX-1"),
        supplier_record("A-2", "Example Vendor LLC"),
        supplier_record("B-1", "Example Vendor LLC", tax_id="TAX-2"),
    ]

    results = resolve_supplier_records(records)

    assert results[1].canonical_entity_id == results[0].canonical_entity_id
    assert results[2].decision is ResolutionDecision.SEPARATE
    assert results[2].canonical_entity_id != results[0].canonical_entity_id


def test_generic_name_tokens_do_not_merge_distinct_supplier_groups() -> None:
    records = [
        supplier_record(
            "ERP-1",
            "Regional Supplier Group 071 Corp",
            country="US",
        ),
        supplier_record(
            "ERP-2",
            "Regional Supplier Group 072 Corp",
            country="US",
        ),
    ]

    results = resolve_supplier_records(records)

    assert results[1].decision is ResolutionDecision.SEPARATE
    assert results[1].canonical_entity_id != results[0].canonical_entity_id
