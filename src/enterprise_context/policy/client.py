from __future__ import annotations

from typing import Any

import httpx

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
            response = client.post(
                f"{self._base_url}/v1/data/procurement/decision",
                json={"input": policy_input.model_dump(mode="json")},
            )
            response.raise_for_status()
            result: Any = response.json().get("result")
            if not isinstance(result, dict):
                raise PolicyServiceError("OPA returned no procurement decision")
            return PolicyDecision.model_validate(result)
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, PolicyServiceError):
                raise
            raise PolicyServiceError("OPA policy evaluation failed") from error
        finally:
            if owns_client:
                client.close()
