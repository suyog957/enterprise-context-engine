from __future__ import annotations

from typing import Any

import httpx

from enterprise_context.observability.metrics import POLICY_DECISIONS, POLICY_DENIAL_REASONS
from enterprise_context.observability.tracing import observed_store_call
from enterprise_context.policy.models import PolicyDecision, ProcurementPolicyInput


class PolicyServiceError(RuntimeError):
    """Raised when OPA cannot provide a valid policy decision."""


class OPAClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 2.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = client

    def evaluate(self, policy_input: ProcurementPolicyInput) -> PolicyDecision:
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout_seconds)
        try:
            with observed_store_call(
                "opa",
                "evaluate",
                **{
                    "ecg.action": policy_input.action,
                    "ecg.resource_id": policy_input.requisition.requisition_id,
                },
            ) as span:
                response = client.post(
                    f"{self._base_url}/v1/data/procurement/decision",
                    json={"input": policy_input.model_dump(mode="json")},
                )
                response.raise_for_status()
                result: Any = response.json().get("result")
                if not isinstance(result, dict):
                    raise PolicyServiceError("OPA returned no procurement decision")
                decision = PolicyDecision.model_validate(result)
                outcome = (
                    "allowed"
                    if decision.allowed
                    else "approval_required"
                    if decision.approval_required
                    else "denied"
                )
                span.set_attribute("ecg.outcome", outcome)
                span.set_attribute("ecg.policy_version", decision.policy_version)
                POLICY_DECISIONS.labels(action=policy_input.action, outcome=outcome).inc()
                for reason in decision.reason_codes:
                    POLICY_DENIAL_REASONS.labels(reason=reason).inc()
                return decision
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, PolicyServiceError):
                raise
            raise PolicyServiceError("OPA policy evaluation failed") from error
        finally:
            if owns_client:
                client.close()

    def evaluate_actions(self, policy_input: ProcurementPolicyInput) -> dict[str, PolicyDecision]:
        """Evaluate every catalog action for the same facts in one OPA call."""
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout_seconds)
        try:
            with observed_store_call(
                "opa",
                "evaluate_actions",
                **{"ecg.resource_id": policy_input.requisition.requisition_id},
            ):
                response = client.post(
                    f"{self._base_url}/v1/data/procurement/action_decisions",
                    json={"input": policy_input.model_dump(mode="json")},
                )
                response.raise_for_status()
                result: Any = response.json().get("result")
                if not isinstance(result, dict) or not result:
                    raise PolicyServiceError("OPA returned no action decisions")
                return {
                    str(action): PolicyDecision.model_validate(value)
                    for action, value in result.items()
                }
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, PolicyServiceError):
                raise
            raise PolicyServiceError("OPA action evaluation failed") from error
        finally:
            if owns_client:
                client.close()
