import json

import httpx
import pytest

from enterprise_context.policy.client import OPAClient, PolicyServiceError
from enterprise_context.policy.models import (
    PrincipalPolicyInput,
    ProcurementPolicyInput,
    RequisitionPolicyInput,
    SupplierPolicyInput,
)


def policy_input() -> ProcurementPolicyInput:
    return ProcurementPolicyInput(
        action="CREATE_PURCHASE_ORDER",
        principal=PrincipalPolicyInput(
            principal_id="user-alice",
            roles=["BUYER"],
            business_unit_ids=["BU-000"],
            approval_limit_minor=1_000_000,
        ),
        requisition=RequisitionPolicyInput(
            requisition_id="PR-1007",
            business_unit_id="BU-000",
            state="APPROVED",
            amount_minor=800_000,
            categories=["Software"],
        ),
        supplier=SupplierPolicyInput(
            canonical_entity_id="supplier-acme",
            status="ACTIVE",
            risk_rating="LOW",
            approved_categories=["Software"],
        ),
    )


def test_opa_client_sends_typed_facts_and_parses_structured_decision() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/data/procurement/decision"
        payload = json.loads(request.content)
        assert payload["input"]["requisition"]["amount_minor"] == 800_000
        return httpx.Response(
            200,
            json={
                "result": {
                    "allowed": True,
                    "approval_required": False,
                    "reason_codes": [],
                    "explanations": [],
                    "policy_version": "0.1.0",
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        decision = OPAClient("http://opa:8181", client=client).evaluate(policy_input())

    assert decision.allowed
    assert decision.policy_version == "0.1.0"


def test_opa_client_fails_closed_on_unavailable_policy_service() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        with pytest.raises(PolicyServiceError):
            OPAClient("http://opa:8181", client=client).evaluate(policy_input())
