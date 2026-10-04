from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row
from scripts.generate_synthetic_data import generate

from enterprise_context.config import Settings
from enterprise_context.domain.models import SupplierSourceRecord
from enterprise_context.domain.requisitions import get_authorized_requisition_context
from enterprise_context.domain.transactions import ProcurementTransactions, TransactionError
from enterprise_context.domain.write_models import ApprovalDecisionRequest, PurchaseOrderCommand
from enterprise_context.entity_resolution.resolver import resolve_supplier_records
from enterprise_context.ingestion.postgres import ingest_generated_data
from enterprise_context.policy.models import PolicyDecision, ProcurementPolicyInput
from enterprise_context.security.principals import PrincipalContext

ROOT = Path(__file__).resolve().parents[2]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


@pytest.mark.integration
def test_postgres_ingestion_retains_and_canonicalizes_seed_data(tmp_path: Path) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    data_root = tmp_path / "data"
    generate(seed=20261003, output_root=data_root)
    raw_root = data_root / "raw" / "generated"
    source_records = [
        SupplierSourceRecord.model_validate(row)
        for row in read_jsonl(raw_root / "supplier_source_records.jsonl")
    ]
    resolutions = resolve_supplier_records(source_records)
    canonical_root = data_root / "canonical" / "generated"
    canonical_root.mkdir(parents=True, exist_ok=True)
    with (canonical_root / "entity_resolution.jsonl").open("w", encoding="utf-8") as output:
        for resolution in resolutions:
            output.write(resolution.model_dump_json() + "\n")

    with psycopg.connect(database_url) as connection:
        result = ingest_generated_data(
            connection, data_root, ROOT / "infra" / "sql" / "001_initial.sql"
        )
        counts = connection.execute(
            """SELECT
                 (SELECT count(*) FROM canonical_supplier),
                 (SELECT count(*) FROM purchase_requisition),
                 (SELECT count(*) FROM purchase_order),
                 (SELECT count(*) FROM contract),
                 (SELECT count(*) FROM principal),
                 (SELECT count(*) FROM purchase_order WHERE canonical_supplier_id IS NULL),
                 (SELECT count(*) FROM purchase_order WHERE amount IS NULL)"""
        ).fetchone()
        requisition = connection.execute(
            """SELECT r.state, r.amount, s.preferred_name, p.principal_id
               FROM purchase_requisition r
               JOIN canonical_supplier s ON s.canonical_entity_id = r.canonical_supplier_id
               JOIN principal p ON p.buyer_id = r.buyer_id
               WHERE r.requisition_id = 'PR-1007'"""
        ).fetchone()

    assert result["canonical_suppliers"] == 100
    assert result["unresolved_purchase_orders"] == 11
    assert counts == (100, 1000, 1000, 200, 50, 11, 50)
    assert requisition == ("APPROVED", 8000, "Acme Corp", "user-alice")

    with psycopg.connect(database_url, row_factory=dict_row) as dict_connection:
        alice_row = dict_connection.execute(
            "SELECT * FROM principal WHERE principal_id = 'user-alice'"
        ).fetchone()
        manager_row = dict_connection.execute(
            "SELECT * FROM principal WHERE principal_id = 'user-buyer-010'"
        ).fetchone()
        dict_connection.execute(
            """UPDATE purchase_requisition
               SET amount = 12000, row_version = row_version + 1
               WHERE requisition_id = 'PR-1007'"""
        )

    assert alice_row is not None and manager_row is not None
    alice = PrincipalContext.model_validate(alice_row)
    manager = PrincipalContext.model_validate(manager_row)

    def policy_evaluator(policy_input: ProcurementPolicyInput) -> PolicyDecision:
        if policy_input.supplier.status == "BLOCKED":
            return PolicyDecision(
                allowed=False,
                approval_required=False,
                reason_codes=["SUPPLIER_BLOCKED"],
                explanations=["Supplier is blocked."],
                policy_version="integration-v1",
            )
        approval_required = (
            policy_input.requisition.amount_minor > policy_input.principal.approval_limit_minor
            or policy_input.supplier.risk_rating == "HIGH"
        )
        approved = policy_input.manager_approval_exists
        return PolicyDecision(
            allowed=not approval_required or approved,
            approval_required=approval_required,
            reason_codes=(
                ["MANAGER_APPROVAL_REQUIRED"] if approval_required and not approved else []
            ),
            explanations=(
                ["Manager approval is required."] if approval_required and not approved else []
            ),
            policy_version="integration-v1",
        )

    dry_run_service = ProcurementTransactions(
        Settings(database_url=database_url, environment="local", dry_run=True),
        policy_evaluator=policy_evaluator,
    )
    dry_run_result = dry_run_service.create_purchase_order(
        "PR-1007", alice, "dry-run-key", command=PurchaseOrderCommand()
    )
    assert dry_run_result.status == "APPROVAL_REQUIRED"
    assert dry_run_result.purchase_order_id is None

    approval = dry_run_service.request_approval("PR-1007", alice)
    with pytest.raises(TransactionError, match="Manager approval permission"):
        dry_run_service.decide_approval(
            approval.approval_id,
            alice,
            ApprovalDecisionRequest(approved=True),
        )
    alice_manager = alice.model_copy(update={"roles": ["BUYER", "MANAGER"]})
    with pytest.raises(TransactionError, match="own actions"):
        dry_run_service.decide_approval(
            approval.approval_id,
            alice_manager,
            ApprovalDecisionRequest(approved=True),
        )
    approved = dry_run_service.decide_approval(
        approval.approval_id,
        manager,
        ApprovalDecisionRequest(approved=True, note="Reviewed synthetic purchase."),
    )
    assert approved.status == "APPROVED"

    write_service = ProcurementTransactions(
        Settings(database_url=database_url, environment="local", dry_run=False),
        policy_evaluator=policy_evaluator,
    )
    current_context = get_authorized_requisition_context("PR-1007", alice)
    assert current_context is not None
    assert write_service.evaluate_action(current_context, alice).allowed
    command = PurchaseOrderCommand(approval_id=approval.approval_id)
    created = write_service.create_purchase_order("PR-1007", alice, "create-pr-1007", command)
    replay = write_service.create_purchase_order("PR-1007", alice, "create-pr-1007", command)
    with psycopg.connect(database_url) as connection:
        resulting_state = connection.execute(
            "SELECT state FROM purchase_requisition WHERE requisition_id = 'PR-1007'"
        ).fetchone()
        created_count_row = connection.execute(
            "SELECT count(*) FROM purchase_order WHERE requisition_id = 'PR-1007'"
        ).fetchone()
        outbox_count_row = connection.execute(
            """SELECT count(*) FROM projection_outbox
               WHERE aggregate_type = 'PurchaseOrder'
                 AND aggregate_id = %s
                 AND event_type = 'PURCHASE_ORDER_CREATED'""",
            (created.purchase_order_id,),
        ).fetchone()

    assert created.status == "CREATED"
    assert replay.idempotent_replay
    assert replay.purchase_order_id == created.purchase_order_id
    assert resulting_state == ("CONVERTED",)
    assert created_count_row is not None
    assert created_count_row[0] == 1
    assert outbox_count_row is not None
    assert outbox_count_row[0] == 1

    with pytest.raises(TransactionError, match="SUPPLIER_BLOCKED"):
        write_service.create_purchase_order(
            "PR-1011", alice, "blocked-pr-1011", command=PurchaseOrderCommand()
        )
