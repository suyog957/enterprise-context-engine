import pytest

from enterprise_context.evaluation.policy_metrics import evaluate_policy_cases
from enterprise_context.policy.models import PolicyDecision


def test_policy_metrics_report_action_errors_and_latency_percentiles() -> None:
    decisions = [
        PolicyDecision(
            allowed=True,
            approval_required=False,
            reason_codes=[],
            explanations=[],
            policy_version="test",
        ),
        PolicyDecision(
            allowed=True,
            approval_required=False,
            reason_codes=[],
            explanations=[],
            policy_version="test",
        ),
        PolicyDecision(
            allowed=False,
            approval_required=False,
            reason_codes=["SUPPLIER_BLOCKED"],
            explanations=["Blocked"],
            policy_version="test",
        ),
    ]

    metrics = evaluate_policy_cases(
        expected_allowed=[True, False, False],
        decisions=decisions,
        latencies_ms=[2.0, 4.0, 8.0],
    )

    assert metrics["cases"] == 3
    assert metrics["action_accuracy"] == pytest.approx(2 / 3)
    assert metrics["false_allows"] == 1
    assert metrics["false_blocks"] == 0
    assert metrics["policy_violation_rate"] == pytest.approx(1 / 3)
    assert metrics["latency_p50_ms"] == 4.0
    assert metrics["latency_p95_ms"] == 8.0


def test_policy_metrics_reject_mismatched_case_counts() -> None:
    with pytest.raises(ValueError, match="equal lengths"):
        evaluate_policy_cases([], [
            PolicyDecision(
                allowed=False,
                approval_required=False,
                reason_codes=[],
                explanations=[],
                policy_version="test",
            )
        ], [])
