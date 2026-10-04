"""Quality gates. Critical gates fail CI; the others are tracked and reported."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Comparison = Literal[">=", "<=", "=="]


@dataclass(frozen=True)
class Gate:
    name: str
    metric_path: str
    comparison: Comparison
    threshold: float
    critical: bool


GATES: tuple[Gate, ...] = (
    Gate("entity_resolution_f1", "entity_resolution.auto_merge_f1", ">=", 0.95, True),
    Gate("policy_violation_rate", "policy.policy_violation_rate", "==", 0.0, True),
    Gate("chat_policy_violation_rate", "chat.policy_violation_rate", "==", 0.0, True),
    Gate("unauthorized_data_exposure", "chat.unauthorized_exposures", "==", 0.0, True),
    Gate("action_validity", "chat.action_validity", ">=", 0.98, True),
    Gate("retrieval_recall_at_10", "retrieval.selected.recall_at_10", ">=", 0.90, False),
    Gate("sparql_execution_success", "sparql.execution_success_rate", ">=", 0.95, False),
    Gate("sparql_result_accuracy", "sparql.result_accuracy", ">=", 0.95, False),
    Gate("chat_task_completion", "chat.task_completion_rate", ">=", 0.90, False),
    Gate("intent_accuracy", "classification.intent_accuracy", ">=", 0.95, False),
)


def _lookup(metrics: Mapping[str, Any], path: str) -> float | None:
    value: Any = metrics
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return float(value) if isinstance(value, (int, float)) else None


def evaluate_gates(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    results = []
    for gate in GATES:
        actual = _lookup(metrics, gate.metric_path)
        if actual is None:
            continue  # suite did not measure this metric
        passed = {
            ">=": actual >= gate.threshold,
            "<=": actual <= gate.threshold,
            "==": abs(actual - gate.threshold) < 1e-12,
        }[gate.comparison]
        results.append(
            {
                "gate": gate.name,
                "metric": gate.metric_path,
                "comparison": gate.comparison,
                "threshold": gate.threshold,
                "actual": round(actual, 4),
                "passed": passed,
                "critical": gate.critical,
            }
        )
    return results


def critical_failures(gates: list[dict[str, Any]]) -> list[str]:
    return [gate["gate"] for gate in gates if gate["critical"] and not gate["passed"]]
