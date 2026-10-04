from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from enterprise_context.agents.procurement import (
    AgentExecutionError,
    AgentRequest,
    AgentResponse,
    AgentTraceStep,
)
from enterprise_context.api import app
from enterprise_context.domain.requisitions import RequisitionContext, SupplierContext
from enterprise_context.policy.models import PolicyDecision
from enterprise_context.security.principals import PrincipalContext, get_current_principal


@pytest.fixture
def principal() -> PrincipalContext:
    return PrincipalContext(
        principal_id="user-alice",
        display_name="Alice Morgan",
        buyer_id="BUY-000",
        roles=["BUYER"],
        business_unit_ids=["BU-000"],
        approval_limit_minor=1_000_000,
    )


@pytest.fixture
def api_client(principal: PrincipalContext) -> Iterator[TestClient]:
    app.dependency_overrides[get_current_principal] = lambda: principal
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def response_model() -> AgentResponse:
    return AgentResponse(
        answer="PR-1007 is eligible.",
        requisition_id="PR-1007",
        context=RequisitionContext(
            requisition_id="PR-1007",
            supplier_source_id="SUP-0000",
            canonical_supplier_id="supplier-acme",
            buyer_id="BUY-000",
            buyer_name="Alice Morgan",
            business_unit_id="BU-000",
            state="APPROVED",
            amount=Decimal("8000.00"),
            currency="USD",
            product_ids=["PROD-00001"],
            categories=["Software"],
            supplier=SupplierContext(
                canonical_entity_id="supplier-acme",
                preferred_name="Acme Corp",
                status="ACTIVE",
                risk_rating="LOW",
                approved_categories=["Software"],
            ),
            active_contract_ids=["CON-2001"],
            row_version=1,
        ),
        policy_decision=PolicyDecision(
            allowed=True,
            approval_required=False,
            reason_codes=[],
            explanations=[],
            policy_version="test-v1",
        ),
        available_actions=["CREATE_PURCHASE_ORDER"],
        trajectory=[
            AgentTraceStep(
                node="determine_allowed_actions",
                status="completed",
                detail="OPA policy test-v1: allowed",
            )
        ],
    )


def test_chat_route_returns_grounded_answer_and_trajectory(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class StubAgent:
        def invoke(
            self, request: AgentRequest, current_principal: PrincipalContext
        ) -> AgentResponse:
            assert request.question.startswith("Can PR-1007")
            assert current_principal.principal_id == "user-alice"
            return response_model()

    monkeypatch.setattr("enterprise_context.api.get_procurement_agent", lambda: StubAgent())

    response = api_client.post(
        "/chat",
        headers={"X-Dev-Principal": "user-alice"},
        json={"question": "Can PR-1007 become a purchase order?"},
    )

    assert response.status_code == 200
    assert response.json()["available_actions"] == ["CREATE_PURCHASE_ORDER"]
    assert response.json()["trajectory"][0]["node"] == "determine_allowed_actions"


def test_chat_route_maps_hidden_context_to_not_found(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class HiddenContextAgent:
        def invoke(
            self, request: AgentRequest, current_principal: PrincipalContext
        ) -> AgentResponse:
            raise AgentExecutionError(
                "Requisition not found or outside principal scope",
                status_code=404,
            )

    monkeypatch.setattr(
        "enterprise_context.api.get_procurement_agent", lambda: HiddenContextAgent()
    )

    response = api_client.post(
        "/chat",
        headers={"X-Dev-Principal": "user-alice"},
        json={"question": "Can PR-1007 become a purchase order?"},
    )

    assert response.status_code == 404
