import json
from pathlib import Path
from typing import Any

from scripts.generate_synthetic_data import generate


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_generator_creates_expected_dataset_sizes_and_source_provenance(
    tmp_path: Path,
) -> None:
    generate(seed=20261003, output_root=tmp_path)

    expected_counts = {
        "suppliers.jsonl": 100,
        "supplier_source_records.jsonl": 203,
        "business_units.jsonl": 10,
        "buyers.jsonl": 50,
        "principals.jsonl": 50,
        "products.jsonl": 500,
        "purchase_requisitions.jsonl": 1000,
        "purchase_orders.jsonl": 1000,
        "contracts.jsonl": 200,
        "documents.jsonl": 58,
    }
    for filename, expected_count in expected_counts.items():
        records = read_jsonl(tmp_path / "raw" / "generated" / filename)
        assert len(records) == expected_count
        assert all(record["source_system"] for record in records)


def test_generator_preserves_required_demo_and_data_quality_cases(tmp_path: Path) -> None:
    generate(seed=20261003, output_root=tmp_path)
    raw = tmp_path / "raw" / "generated"
    requisitions = read_jsonl(raw / "purchase_requisitions.jsonl")
    suppliers = read_jsonl(raw / "suppliers.jsonl")
    orders = read_jsonl(raw / "purchase_orders.jsonl")
    principals = read_jsonl(raw / "principals.jsonl")

    allowed_demo = next(record for record in requisitions if record["requisition_id"] == "PR-1007")
    blocked_demo = next(record for record in requisitions if record["requisition_id"] == "PR-1011")
    approval_demo = next(record for record in requisitions if record["requisition_id"] == "PR-1012")
    assert allowed_demo["state"] == "APPROVED"
    assert allowed_demo["supplier_id"] == "SUP-0000"
    assert allowed_demo["amount"] == "8000.00"
    assert (
        next(order for order in orders if order["purchase_order_id"] == "PO-5007")["requisition_id"]
        is None
    )
    assert blocked_demo["supplier_id"] == "SUP-0001"
    assert approval_demo["state"] == "APPROVED"
    assert approval_demo["buyer_id"] == "BUY-000"
    assert approval_demo["amount"] == "18000.00"
    assert approval_demo["supplier_id"] == "SUP-0008"
    assert approval_demo["product_ids"] == ["PROD-00000"]
    assert suppliers[8]["status"] == "ACTIVE"
    assert suppliers[8]["risk_rating"] == "LOW"
    assert suppliers[0]["status"] == "ACTIVE"
    assert suppliers[1]["status"] == "BLOCKED"
    assert any(order["amount"] is None for order in orders)
    assert any(order["supplier_id"].startswith("UNKNOWN-") for order in orders)
    assert next(principal for principal in principals if principal["principal_id"] == "user-admin")[
        "roles"
    ] == ["ADMIN"]
    assert next(
        principal for principal in principals if principal["principal_id"] == "user-auditor"
    )["roles"] == ["AUDITOR"]

    workflow_cases = read_jsonl(tmp_path / "golden" / "generated" / "workflow_cases.jsonl")
    entity_pairs = read_jsonl(tmp_path / "golden" / "generated" / "entity_pairs.jsonl")
    assert len(workflow_cases) == 75
    assert len(entity_pairs) == 282
    acme_aliases = {
        record["supplier_name"]
        for record in read_jsonl(raw / "supplier_source_records.jsonl")
        if record["external_supplier_id"].endswith("-0000")
    }
    assert {"Acme Corp", "ACME CORP", "Acme Corpp", "ACME"}.issubset(acme_aliases)
    assert all(
        record["source_system"] == "golden_dataset" for record in workflow_cases + entity_pairs
    )


def test_source_record_ids_are_unique_within_each_source_system(tmp_path: Path) -> None:
    generate(seed=20261003, output_root=tmp_path)
    raw_root = tmp_path / "raw" / "generated"
    source_keys: list[tuple[str, str]] = []
    for path in raw_root.glob("*.jsonl"):
        source_keys.extend(
            (record["source_system"], record["source_record_id"]) for record in read_jsonl(path)
        )

    assert len(source_keys) == len(set(source_keys))
