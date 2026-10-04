from collections.abc import Sequence
from statistics import median

from enterprise_context.policy.models import PolicyDecision


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def evaluate_policy_cases(
    expected_allowed: Sequence[bool],
    decisions: Sequence[PolicyDecision],
    latencies_ms: Sequence[float],
) -> dict[str, float | int]:
    if len(expected_allowed) != len(decisions):
        raise ValueError("Expected labels and policy decisions must have equal lengths")
    total = len(decisions)
    correct = sum(
        expected == decision.allowed
        for expected, decision in zip(expected_allowed, decisions, strict=True)
    )
    false_allows = sum(
        expected is False and decision.allowed
        for expected, decision in zip(expected_allowed, decisions, strict=True)
    )
    false_blocks = sum(
        expected is True and not decision.allowed
        for expected, decision in zip(expected_allowed, decisions, strict=True)
    )
    return {
        "cases": total,
        "action_accuracy": correct / total if total else 0.0,
        "false_allows": false_allows,
        "false_blocks": false_blocks,
        "policy_violation_rate": false_allows / total if total else 0.0,
        "latency_p50_ms": median(latencies_ms) if latencies_ms else 0.0,
        "latency_p95_ms": _percentile(latencies_ms, 0.95),
    }
