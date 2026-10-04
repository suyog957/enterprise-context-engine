from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from enterprise_context.config import get_settings
from enterprise_context.evaluation.policy_metrics import evaluate_policy_cases
from enterprise_context.policy.client import OPAClient
from enterprise_context.policy.local import evaluate_procurement_policy
from enterprise_context.policy.models import (
    PrincipalPolicyInput,
    ProcurementPolicyInput,
    RequisitionPolicyInput,
    SupplierPolicyInput,
)

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate OPA against the synthetic golden workflow set."
    )
    parser.add_argument("--smoke", action="store_true", help="Run a fast first-ten-case smoke set")
    parser.add_argument("--limit", type=int, default=75)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument(
        "--policy-backend",
        choices=["local", "opa"],
        default="local",
        help="Use the deterministic local evaluator or a live OPA service.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "evaluation" / "generated" / "policy_latest.json",
    )
    args = parser.parse_args()

    raw_root = ROOT / "data" / "raw" / "generated"
    golden_root = ROOT / "data" / "golden" / "generated"
    suppliers = {row["supplier_id"]: row for row in read_jsonl(raw_root / "suppliers.jsonl")}
    buyers = {row["buyer_id"]: row for row in read_jsonl(raw_root / "buyers.jsonl")}
    requisitions = {
        row["requisition_id"]: row for row in read_jsonl(raw_root / "purchase_requisitions.jsonl")
    }
    products = {row["product_id"]: row for row in read_jsonl(raw_root / "products.jsonl")}
    golden = read_jsonl(golden_root / "workflow_cases.jsonl")
    limit = min(10, args.limit) if args.smoke else args.limit
    cases = golden[:limit]
    client = OPAClient(get_settings().opa_url) if args.policy_backend == "opa" else None
    decisions = []
    expected: list[bool] = []
    latencies_ms: list[float] = []

    for case in cases:
        requisition = requisitions[case["requisition_id"]]
        supplier = suppliers[requisition["supplier_id"]]
        buyer = buyers[requisition["buyer_id"]]
        categories = sorted(
            {products[product_id]["category"] for product_id in requisition["product_ids"]}
        )
        policy_input = ProcurementPolicyInput(
            action="CREATE_PURCHASE_ORDER",
            principal=PrincipalPolicyInput(
                principal_id=f"user-{buyer['buyer_id'].lower()}",
                roles=buyer["roles"],
                business_unit_ids=[buyer["business_unit_id"]],
                approval_limit_minor=int(Decimal(buyer["approval_limit"]) * 100),
            ),
            requisition=RequisitionPolicyInput(
                requisition_id=requisition["requisition_id"],
                business_unit_id=requisition["business_unit_id"],
                state=requisition["state"],
                amount_minor=int(Decimal(requisition["amount"]) * 100),
                categories=categories,
            ),
            supplier=SupplierPolicyInput(
                canonical_entity_id=supplier["supplier_id"],
                status=supplier["status"],
                risk_rating=supplier["risk_rating"],
                approved_categories=supplier["approved_categories"],
            ),
            manager_approval_exists=False,
        )
        started = time.perf_counter()
        decision = (
            client.evaluate(policy_input)
            if client is not None
            else evaluate_procurement_policy(policy_input)
        )
        latencies_ms.append((time.perf_counter() - started) * 1000)
        decisions.append(decision)
        expected.append(bool(case["expected_allowed"]))

    metrics = evaluate_policy_cases(expected, decisions, latencies_ms)
    mismatches = [
        {
            "case_id": case["case_id"],
            "requisition_id": case["requisition_id"],
            "expected_allowed": is_expected,
            "actual_allowed": decision.allowed,
            "reason_codes": decision.reason_codes,
        }
        for case, is_expected, decision in zip(cases, expected, decisions, strict=True)
        if is_expected != decision.allowed
    ]
    report = {
        "dataset_seed": 20261003,
        "policy_backend": args.policy_backend,
        "policy_version": decisions[0].policy_version if decisions else "unknown",
        "metrics": metrics,
        "mismatches": mismatches,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if args.fail_on_regression and mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
