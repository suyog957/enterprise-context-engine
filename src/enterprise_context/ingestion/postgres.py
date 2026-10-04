from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from enterprise_context.domain.models import EntityResolutionResult
from enterprise_context.entity_resolution.normalization import normalize_supplier_name
from enterprise_context.migrations import apply_migrations


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def execute_many(
    connection: psycopg.Connection[Any], query: str, rows: list[tuple[Any, ...]]
) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(query, rows)


def build_erp_supplier_index(
    source_records: list[dict[str, Any]],
    resolutions: dict[str, EntityResolutionResult],
) -> dict[str, str]:
    return {
        str(record["source_supplier_id"]): resolutions[
            str(record["source_record_id"])
        ].canonical_entity_id
        for record in source_records
        if record["source_system"] == "ERP"
        and resolutions[str(record["source_record_id"])].decision.value != "review"
    }


def build_purchase_order_row(
    record: dict[str, Any], erp_supplier_index: dict[str, str]
) -> tuple[Any, ...]:
    canonical_id = erp_supplier_index.get(str(record["supplier_id"]))
    issues = [] if canonical_id else ["UNRESOLVED_SUPPLIER"]
    amount: Decimal | None = None
    if record.get("amount") is not None:
        try:
            amount = Decimal(str(record["amount"]))
        except InvalidOperation:
            issues.append("INVALID_AMOUNT")
    return (
        record["purchase_order_id"],
        record.get("requisition_id"),
        record["supplier_id"],
        canonical_id,
        record["buyer_id"],
        amount,
        record["currency"],
        record["status"],
        Jsonb(issues),
        record["source_system"],
        datetime.fromisoformat(str(record["ordered_at"])) if record.get("ordered_at") else None,
    )


def build_canonical_supplier_rows(
    supplier_records: list[dict[str, Any]],
    erp_supplier_index: dict[str, str],
) -> dict[str, tuple[Any, ...]]:
    supplier_by_id = {str(row["supplier_id"]): row for row in supplier_records}
    canonical_rows: dict[str, tuple[Any, ...]] = {}
    for source_supplier_id, canonical_id in erp_supplier_index.items():
        record = supplier_by_id.get(source_supplier_id)
        if record is None:
            continue
        canonical_rows[canonical_id] = (
            canonical_id,
            record["supplier_name"],
            record["country_code"],
            record.get("postal_code"),
            record["status"],
            record["risk_rating"],
            record["approved_categories"],
            record["source_system"],
        )
    return canonical_rows


def build_source_supplier_rows(
    source_records: list[dict[str, Any]],
    resolutions: dict[str, EntityResolutionResult],
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for record in source_records:
        resolution = resolutions[str(record["source_record_id"])]
        rows.append(
            (
                record["source_system"],
                record["source_supplier_id"],
                None if resolution.decision.value == "review" else resolution.canonical_entity_id,
                record["source_record_id"],
                resolution.resolution_method.value,
                resolution.confidence_score,
                resolution.decision.value == "review",
            )
        )
    return rows


def build_supplier_alias_rows(
    source_records: list[dict[str, Any]],
    resolutions: dict[str, EntityResolutionResult],
    canonical_ids: set[str],
) -> list[tuple[Any, ...]]:
    """Every observed supplier name, normalized for query-time resolution.

    Review candidates keep a NULL canonical link and record the proposed entity
    separately, so an unreviewed match is never presented as a confirmed merge.
    """
    rows: list[tuple[Any, ...]] = []
    for record in source_records:
        resolution = resolutions[str(record["source_record_id"])]
        review = resolution.decision.value == "review"
        canonical_id = None if review else resolution.canonical_entity_id
        candidate_id = resolution.candidate_entity_id if review else None
        rows.append(
            (
                record["source_system"],
                record["source_record_id"],
                canonical_id if canonical_id in canonical_ids else None,
                candidate_id if candidate_id in canonical_ids else None,
                record["supplier_name"],
                normalize_supplier_name(str(record["supplier_name"])),
                resolution.resolution_method.value,
                resolution.confidence_score,
                review,
            )
        )
    return rows


def ingest_generated_data(
    connection: psycopg.Connection[Any], data_root: Path, migrations_dir: Path
) -> dict[str, Any]:
    raw_root = data_root / "raw" / "generated"
    canonical_root = data_root / "canonical" / "generated"
    if not (raw_root / "suppliers.jsonl").exists():
        raise FileNotFoundError("Synthetic data is missing; run generate_synthetic_data.py first.")
    if not (canonical_root / "entity_resolution.jsonl").exists():
        raise FileNotFoundError("Entity resolution is missing; run resolve_entities.py first.")

    raw_files = sorted(raw_root.glob("*.jsonl"))
    raw_records_by_type = {path.stem: read_jsonl(path) for path in raw_files}
    resolution_rows = read_jsonl(canonical_root / "entity_resolution.jsonl")
    resolution_by_source = {
        str(row["source_record_id"]): EntityResolutionResult.model_validate(row)
        for row in resolution_rows
    }
    source_supplier_records = raw_records_by_type["supplier_source_records"]
    for record in source_supplier_records:
        result = resolution_by_source[str(record["source_record_id"])]
        record["canonical_entity_id"] = result.canonical_entity_id
        record["resolution_method"] = result.resolution_method.value
        record["confidence_score"] = result.confidence_score
    erp_source_to_canonical = build_erp_supplier_index(
        source_supplier_records, resolution_by_source
    )

    source_record_rows: list[tuple[str, str, str, datetime | None, Jsonb]] = []
    for record_type, records in raw_records_by_type.items():
        for record in records:
            source_record_rows.append(
                (
                    str(record["source_system"]),
                    str(record["source_record_id"]),
                    record_type,
                    (
                        datetime.fromisoformat(str(record["observed_at"]))
                        if record.get("observed_at")
                        else None
                    ),
                    Jsonb(record),
                )
            )

    run_id = uuid4()
    record_counts = Counter({kind: len(records) for kind, records in raw_records_by_type.items()})
    with connection.transaction():
        apply_migrations(connection, migrations_dir)
        connection.execute(
            "INSERT INTO ingestion_run (run_id, status, record_counts) VALUES (%s, 'RUNNING', %s)",
            (run_id, Jsonb(dict(record_counts))),
        )
        execute_many(
            connection,
            """INSERT INTO source_record
               (source_system, source_record_id, record_type, observed_at, payload)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (source_system, source_record_id) DO UPDATE SET
                 record_type = EXCLUDED.record_type,
                 observed_at = EXCLUDED.observed_at,
                 payload = EXCLUDED.payload,
                 ingested_at = now()""",
            source_record_rows,
        )

        execute_many(
            connection,
            """INSERT INTO business_unit (business_unit_id, name, source_system)
               VALUES (%s, %s, %s)
               ON CONFLICT (business_unit_id) DO UPDATE SET
                 name = EXCLUDED.name, source_system = EXCLUDED.source_system""",
            [
                (row["business_unit_id"], row["name"], row["source_system"])
                for row in raw_records_by_type["business_units"]
            ],
        )

        canonical_supplier_rows = build_canonical_supplier_rows(
            raw_records_by_type["suppliers"], erp_source_to_canonical
        )
        execute_many(
            connection,
            """INSERT INTO canonical_supplier
               (canonical_entity_id, preferred_name, country_code, postal_code, status,
                risk_rating, approved_categories, source_system)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (canonical_entity_id) DO UPDATE SET
                 preferred_name = EXCLUDED.preferred_name,
                 country_code = EXCLUDED.country_code,
                 postal_code = EXCLUDED.postal_code,
                 status = EXCLUDED.status,
                 risk_rating = EXCLUDED.risk_rating,
                 approved_categories = EXCLUDED.approved_categories,
                 source_system = EXCLUDED.source_system,
                 updated_at = now()""",
            list(canonical_supplier_rows.values()),
        )

        execute_many(
            connection,
            """INSERT INTO source_supplier_identifier
               (source_system, source_supplier_id, canonical_entity_id, source_record_id,
                resolution_method, confidence_score, review_required)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_system, source_supplier_id) DO UPDATE SET
                 canonical_entity_id = EXCLUDED.canonical_entity_id,
                 source_record_id = EXCLUDED.source_record_id,
                 resolution_method = EXCLUDED.resolution_method,
                 confidence_score = EXCLUDED.confidence_score,
                 review_required = EXCLUDED.review_required""",
            build_source_supplier_rows(source_supplier_records, resolution_by_source),
        )

        execute_many(
            connection,
            """INSERT INTO supplier_alias
               (source_system, source_record_id, canonical_entity_id, candidate_entity_id,
                alias, normalized_alias, resolution_method, confidence_score, review_required)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_system, source_record_id) DO UPDATE SET
                 canonical_entity_id = EXCLUDED.canonical_entity_id,
                 candidate_entity_id = EXCLUDED.candidate_entity_id,
                 alias = EXCLUDED.alias,
                 normalized_alias = EXCLUDED.normalized_alias,
                 resolution_method = EXCLUDED.resolution_method,
                 confidence_score = EXCLUDED.confidence_score,
                 review_required = EXCLUDED.review_required""",
            build_supplier_alias_rows(
                source_supplier_records, resolution_by_source, set(canonical_supplier_rows)
            ),
        )

        execute_many(
            connection,
            """INSERT INTO buyer
               (buyer_id, name, business_unit_id, approval_limit, roles, source_system)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (buyer_id) DO UPDATE SET name = EXCLUDED.name,
                 business_unit_id = EXCLUDED.business_unit_id,
                 approval_limit = EXCLUDED.approval_limit,
                 roles = EXCLUDED.roles, source_system = EXCLUDED.source_system""",
            [
                (
                    row["buyer_id"], row["name"], row["business_unit_id"],
                    Decimal(row["approval_limit"]), row["roles"], row["source_system"]
                )
                for row in raw_records_by_type["buyers"]
            ],
        )
        execute_many(
            connection,
            """INSERT INTO principal
               (principal_id, display_name, buyer_id, roles, business_unit_ids,
                approval_limit_minor, active, source_system)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (principal_id) DO UPDATE SET
                 display_name = EXCLUDED.display_name,
                 buyer_id = EXCLUDED.buyer_id,
                 roles = EXCLUDED.roles,
                 business_unit_ids = EXCLUDED.business_unit_ids,
                 approval_limit_minor = EXCLUDED.approval_limit_minor,
                 active = EXCLUDED.active,
                 source_system = EXCLUDED.source_system""",
            [
                (
                    row["principal_id"], row["display_name"], row["buyer_id"],
                    row["roles"], row["business_unit_ids"],
                    row["approval_limit_minor"], row["active"], row["source_system"],
                )
                for row in raw_records_by_type["principals"]
            ],
        )
        execute_many(
            connection,
            """INSERT INTO product (product_id, name, category, unit_of_measure, source_system)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (product_id) DO UPDATE SET name = EXCLUDED.name,
                 category = EXCLUDED.category, unit_of_measure = EXCLUDED.unit_of_measure,
                 source_system = EXCLUDED.source_system""",
            [
                (
                    row["product_id"],
                    row["name"],
                    row["category"],
                    row["unit_of_measure"],
                    row["source_system"],
                )
                for row in raw_records_by_type["products"]
            ],
        )

        execute_many(
            connection,
            """INSERT INTO purchase_requisition
               (requisition_id, source_supplier_id, canonical_supplier_id, buyer_id,
                business_unit_id, state, amount, currency, product_ids, created_at, source_system)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (requisition_id) DO UPDATE SET
                 source_supplier_id = EXCLUDED.source_supplier_id,
                 canonical_supplier_id = EXCLUDED.canonical_supplier_id,
                 buyer_id = EXCLUDED.buyer_id,
                 business_unit_id = EXCLUDED.business_unit_id,
                 state = EXCLUDED.state,
                 amount = EXCLUDED.amount,
                 currency = EXCLUDED.currency,
                 product_ids = EXCLUDED.product_ids,
                 created_at = EXCLUDED.created_at,
                 source_system = EXCLUDED.source_system,
                 row_version = purchase_requisition.row_version + 1,
                 updated_at = now()""",
            [
                (
                    row["requisition_id"], row["supplier_id"],
                    erp_source_to_canonical.get(str(row["supplier_id"])), row["buyer_id"],
                    row["business_unit_id"], row["state"], Decimal(row["amount"]),
                    row["currency"], Jsonb(row["product_ids"]),
                    datetime.fromisoformat(row["created_at"]), row["source_system"],
                )
                for row in raw_records_by_type["purchase_requisitions"]
            ],
        )

        po_rows = [
            build_purchase_order_row(row, erp_source_to_canonical)
            for row in raw_records_by_type["purchase_orders"]
        ]
        execute_many(
            connection,
            """INSERT INTO purchase_order
               (purchase_order_id, requisition_id, source_supplier_id, canonical_supplier_id,
                buyer_id, amount, currency, status, data_quality_issues, source_system,
                created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
               ON CONFLICT (purchase_order_id) DO UPDATE SET
                 requisition_id = EXCLUDED.requisition_id,
                 source_supplier_id = EXCLUDED.source_supplier_id,
                 canonical_supplier_id = EXCLUDED.canonical_supplier_id,
                 buyer_id = EXCLUDED.buyer_id,
                 amount = EXCLUDED.amount,
                 currency = EXCLUDED.currency,
                 status = EXCLUDED.status,
                 data_quality_issues = EXCLUDED.data_quality_issues,
                 source_system = EXCLUDED.source_system,
                 created_at = EXCLUDED.created_at""",
            po_rows,
        )

        execute_many(
            connection,
            """INSERT INTO contract
               (contract_id, source_supplier_id, canonical_supplier_id, start_date, end_date,
                permitted_categories, status, source_system)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (contract_id) DO UPDATE SET
                 source_supplier_id = EXCLUDED.source_supplier_id,
                 canonical_supplier_id = EXCLUDED.canonical_supplier_id,
                 start_date = EXCLUDED.start_date,
                 end_date = EXCLUDED.end_date,
                 permitted_categories = EXCLUDED.permitted_categories,
                 status = EXCLUDED.status,
                 source_system = EXCLUDED.source_system""",
            [
                (
                    row["contract_id"], row["supplier_id"],
                    erp_source_to_canonical.get(str(row["supplier_id"])),
                    row["start_date"], row["end_date"], row["permitted_categories"],
                    row["status"], row["source_system"],
                )
                for row in raw_records_by_type["contracts"]
            ],
        )
        connection.execute(
            "UPDATE ingestion_run SET status = 'SUCCEEDED', completed_at = now() WHERE run_id = %s",
            (run_id,),
        )

    return {
        "run_id": str(run_id),
        "counts": dict(record_counts),
        "canonical_suppliers": len(canonical_supplier_rows),
        "unresolved_purchase_orders": sum(1 for row in po_rows if row[3] is None),
    }
