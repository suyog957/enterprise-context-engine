from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from enterprise_context.api import app
from enterprise_context.domain.transactions import TransactionError
from enterprise_context.domain.write_models import (
    ActionSimulation,
    ApprovalRequestResult,
    PurchaseOrderCommand,
    PurchaseOrderExecution,
)
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


def decision(*, allowed: bool, approval_required: bool = False) -> PolicyDecision:
    return PolicyDecision(
        allowed=allowed,
        approval_required=approval_required,
        reason_codes=["MANAGER_APPROVAL_REQUIRED"] if approval_required else [],
        explanations=["Manager approval is required."] if approval_required else [],
        policy_version="test-v1",
    )


def test_simulate_route_returns_dry_run_policy_decision(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Transactions:
        def simulate(
            self, requisition_id: str, current_principal: PrincipalContext
        ) -> ActionSimulation:
            assert current_principal.principal_id == "user-alice"
            return ActionSimulation(
                requisition_id=requisition_id,
                decision=decision(allowed=True),
                available=True,
                approval_required=False,
            )

    monkeypatch.setattr(
        "enterprise_context.api.get_procurement_transactions", lambda: Transactions()
    )
    response = api_client.post("/requisitions/PR-1007/simulate-po")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["dry_run"] is True


def test_approval_request_route_returns_pending_request(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval_id = uuid4()

    class Transactions:
        def request_approval(
            self, requisition_id: str, current_principal: PrincipalContext
        ) -> ApprovalRequestResult:
            return ApprovalRequestResult(
                approval_id=approval_id,
                requisition_id=requisition_id,
                status="PENDING",
                expires_at="2026-10-03T12:00:00+00:00",
                policy_version="test-v1",
                reason_codes=["MANAGER_APPROVAL_REQUIRED"],
            )

    monkeypatch.setattr(
        "enterprise_context.api.get_procurement_transactions", lambda: Transactions()
    )
    response = api_client.post("/requisitions/PR-1007/approval-requests")

    assert response.status_code == 201
    assert response.json()["approval_id"] == str(approval_id)
    assert response.json()["status"] == "PENDING"


def test_create_route_requires_idempotency_key(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Transactions:
        def create_purchase_order(
            self,
            requisition_id: str,
            current_principal: PrincipalContext,
            idempotency_key: str,
            command: PurchaseOrderCommand,
        ) -> PurchaseOrderExecution:
            return PurchaseOrderExecution(
                requisition_id="PR-1007",
                purchase_order_id=None,
                status="SIMULATED",
                dry_run=True,
                decision=decision(allowed=True),
            )

    monkeypatch.setattr(
        "enterprise_context.api.get_procurement_transactions", lambda: Transactions()
    )
    response = api_client.post("/requisitions/PR-1007/create-po", json={})

    assert response.status_code == 422


def test_write_service_error_maps_to_conflict(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Transactions:
        def create_purchase_order(
            self,
            requisition_id: str,
            current_principal: PrincipalContext,
            idempotency_key: str,
            command: PurchaseOrderCommand,
        ) -> PurchaseOrderExecution:
            raise TransactionError("blocked by policy")

    monkeypatch.setattr(
        "enterprise_context.api.get_procurement_transactions", lambda: Transactions()
    )
    response = api_client.post(
        "/requisitions/PR-1007/create-po",
        headers={"Idempotency-Key": "create-pr-1007"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "blocked by policy"
