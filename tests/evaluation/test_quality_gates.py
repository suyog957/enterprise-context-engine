"""Deterministic evaluation gates, run in CI on freshly generated data (no services)."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.generate_synthetic_data import generate
from scripts.run_evaluation import (
    classification_metrics,
    entity_resolution_metrics,
    policy_metrics,
    template_validity,
)

from enterprise_context.evaluation.gates import critical_failures, evaluate_gates


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("evaluation")
    generate(seed=20261003, output_root=root)
    return root


def test_golden_sets_meet_the_brief_minimums(data_root: Path) -> None:
    golden = data_root / "golden" / "generated"

    def count(name: str) -> int:
        return sum(1 for line in (golden / name).read_text(encoding="utf-8").splitlines() if line)

    assert count("chat_cases.jsonl") >= 75
    assert count("retrieval_queries.jsonl") >= 25
    assert count("sparql_cases.jsonl") >= 15


def test_offline_critical_gates_pass(data_root: Path) -> None:
    raw = data_root / "raw" / "generated"
    golden = data_root / "golden" / "generated"
    metrics = {
        "entity_resolution": entity_resolution_metrics(raw, golden),
        "policy": policy_metrics("local", 75, raw, golden),
        "classification": classification_metrics(golden),
    }
    gates = evaluate_gates(metrics)

    assert critical_failures(gates) == []
    assert metrics["policy"]["false_allows"] == 0
    assert metrics["classification"]["intent_accuracy"] >= 0.95, metrics["classification"]["errors"]


def test_every_sparql_template_is_valid_for_scoped_and_global_principals() -> None:
    assert template_validity()["invalid"] == []
