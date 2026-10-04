"""Concurrent conversion of one requisition must create exactly one purchase order."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from scripts.generate_synthetic_data import generate

from enterprise_context.config import Settings
from enterprise_context.domain.models import SupplierSourceRecord
from enterprise_context.domain.transactions import ProcurementTransactions, TransactionError
from enterprise_context.domain.write_models import PurchaseOrderCommand, PurchaseOrderExecution
from enterprise_context.entity_resolution.resolver import resolve_supplier_records
from enterprise_context.ingestion.postgres import ingest_generated_data, read_jsonl
from enterprise_context.policy.models import PolicyDecision, ProcurementPolicyInput
from enterprise_context.security.principals import PrincipalContext

ROOT = Path(__file__).resolve().parents[2]
REQUISITION = "PR-1003"  # APPROVED, outside the PO-linked range PR-1101..PR-2000


def allow_all(policy_input: ProcurementPolicyInput) -> PolicyDecision:
    return PolicyDecision(
        allowed=True,
        approval_required=False,
        reason_codes=[],
        explanations=[],
        policy_version="concurrency-test",
    )


@pytest.mark.integration
def test_parallel_conversions_with_distinct_keys_create_one_purchase_order(
    tmp_path: Path,
) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    data_root = tmp_path / "data"
    generate(seed=20261003, output_root=data_root)
    records = [
        SupplierSourceRecord.model_validate(row)
        for row in read_jsonl(data_root / "raw" / "generated" / "supplier_source_records.jsonl")
    ]
    canonical = data_root / "canonical" / "generated"
    canonical.mkdir(parents=True, exist_ok=True)
    with (canonical / "entity_resolution.jsonl").open("w", encoding="utf-8") as output:
        for resolution in resolve_supplier_records(records):
            output.write(resolution.model_dump_json() + "\n")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        ingest_generated_data(connection, data_root, ROOT / "infra" / "sql")
        connection.execute("DELETE FROM purchase_order WHERE requisition_id = %s", (REQUISITION,))
        connection.execute(
            "UPDATE purchase_requisition SET state = 'APPROVED' WHERE requisition_id = %s",
            (REQUISITION,),
        )
        admin_row = connection.execute(
            "SELECT * FROM principal WHERE principal_id = 'user-admin'"
        ).fetchone()
    assert admin_row is not None
    admin = PrincipalContext.model_validate(admin_row)
    service = ProcurementTransactions(
        Settings(database_url=database_url, environment="local", dry_run=False),
        policy_evaluator=allow_all,
    )

    def convert(attempt: int) -> PurchaseOrderExecution | str:
        try:
            return service.create_purchase_order(
                REQUISITION, admin, f"parallel-{attempt}", PurchaseOrderCommand()
            )
        except (TransactionError, psycopg.Error) as error:
            return type(error).__name__

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(convert, range(8)))

    created = [o for o in outcomes if isinstance(o, PurchaseOrderExecution)]
    with psycopg.connect(database_url) as check:
        count = check.execute(
            "SELECT count(*) FROM purchase_order WHERE requisition_id = %s", (REQUISITION,)
        ).fetchone()
        events = check.execute(
            """SELECT count(*) FROM projection_outbox
               WHERE event_type = 'PURCHASE_ORDER_CREATED'
                 AND payload ->> 'requisition_id' = %s""",
            (REQUISITION,),
        ).fetchone()

    assert len(created) == 1
    assert created[0].status == "CREATED"
    assert count == (1,)
    assert events == (1,)
