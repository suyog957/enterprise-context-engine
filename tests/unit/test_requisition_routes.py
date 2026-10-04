from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from enterprise_context.api import app
from enterprise_context.domain.requisitions import RequisitionContext, SupplierContext
from enterprise_context.policy.client import PolicyServiceError
from enterprise_context.policy.models import PolicyDecision
from enterprise_context.security.principals import PrincipalContext, get_current_principal


@pytest.fixture
def principal() -> PrincipalContext:
    return PrincipalContext(
        principal_id="user-alice",
        display_name="Alice Morgan",
        buyer_id="BUY-000",
        roles=["BUYER", "MANAGER"],
        business_unit_ids=["BU-000"],
        approval_limit_minor=1_000_000,
    )


@pytest.fixture
def api_client(principal: PrincipalContext) -> Iterator[TestClient]:
    app.dependency_overrides[get_current_principal] = lambda: principal
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def requisition_context() -> RequisitionContext:
    return RequisitionContext(
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
    )


def test_out_of_scope_requisition_is_not_found(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "enterprise_context.api.get_authorized_requisition_context",
        lambda requisition_id, principal: None,
    )

    response = api_client.get("/requisitions/PR-1007")

    assert response.status_code == 404


def test_allowed_action_is_only_exposed_after_policy_allows_it(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "enterprise_context.api.get_authorized_requisition_context",
        lambda requisition_id, principal: requisition_context(),
    )

    class AllowingTransactions:
        def evaluate_action(
            self, context: RequisitionContext, principal: PrincipalContext
        ) -> PolicyDecision:
            assert context.amount == Decimal("8000.00")
            return PolicyDecision(
                allowed=True,
                approval_required=False,
                reason_codes=[],
                explanations=[],
                policy_version="test-policy",
            )

    monkeypatch.setattr(
        "enterprise_context.api.get_procurement_transactions",
        lambda: AllowingTransactions(),
    )

    response = api_client.get("/requisitions/PR-1007/allowed-actions")

    assert response.status_code == 200
    assert response.json()["available_actions"] == ["CREATE_PURCHASE_ORDER"]
    assert response.json()["actions"][0]["status"] == "AVAILABLE"


def test_opa_outage_fails_closed_for_action_discovery(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "enterprise_context.api.get_authorized_requisition_context",
        lambda requisition_id, principal: requisition_context(),
    )

    class UnavailableTransactions:
        def evaluate_action(
            self, context: RequisitionContext, principal: PrincipalContext
        ) -> PolicyDecision:
            raise PolicyServiceError("unavailable")

    monkeypatch.setattr(
        "enterprise_context.api.get_procurement_transactions",
        lambda: UnavailableTransactions(),
    )

    response = api_client.get("/requisitions/PR-1007/allowed-actions")

    assert response.status_code == 503
    assert response.json()["detail"] == "policy_service_unavailable"
