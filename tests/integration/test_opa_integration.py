"""The real Rego bundle evaluated by a live OPA server, and fail-closed behaviour."""

from __future__ import annotations

import os

import pytest

from enterprise_context.policy.client import OPAClient, PolicyServiceError
from enterprise_context.policy.models import (
    PrincipalPolicyInput,
    ProcurementPolicyInput,
    RequisitionPolicyInput,
    SupplierPolicyInput,
)

pytestmark = pytest.mark.integration


def policy_input(**requisition: object) -> ProcurementPolicyInput:
    values: dict[str, object] = {
        "requisition_id": "PR-1",
        "business_unit_id": "BU-000",
        "state": "APPROVED",
        "amount_minor": 800_000,
        "categories": ["Software"],
    }
    values.update(requisition)
    return ProcurementPolicyInput(
        action="CREATE_PURCHASE_ORDER",
        principal=PrincipalPolicyInput(
            principal_id="user-alice",
            roles=["BUYER"],
            business_unit_ids=["BU-000"],
            approval_limit_minor=1_000_000,
        ),
        requisition=RequisitionPolicyInput.model_validate(values),
        supplier=SupplierPolicyInput(
            canonical_entity_id="supplier-x",
            status="ACTIVE",
            risk_rating="LOW",
            approved_categories=["Software"],
        ),
    )


@pytest.fixture
def client() -> OPAClient:
    url = os.environ.get("OPA_URL")
    if not url or not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("OPA_URL and TEST_DATABASE_URL are required")
    return OPAClient(url)


def test_live_bundle_decisions_cover_allow_approval_and_block(client: OPAClient) -> None:
    allowed = client.evaluate(policy_input())
    approval = client.evaluate(policy_input(amount_minor=5_000_000))
    blocked = client.evaluate(policy_input(state="SUBMITTED", amount_minor=5_000_000))

    assert allowed.allowed and allowed.reason_codes == []
    assert approval.approval_required and approval.reason_codes == ["MANAGER_APPROVAL_REQUIRED"]
    assert not blocked.allowed and not blocked.approval_required
    assert [rule.rule_id for rule in blocked.policy_rules] == ["REQ-001"]


def test_action_catalog_is_evaluated_from_the_same_bundle(client: OPAClient) -> None:
    decisions = client.evaluate_actions(policy_input())

    assert decisions["CREATE_PURCHASE_ORDER"].allowed
    assert decisions["CANCEL_REQUISITION"].allowed
    assert decisions["EDIT_SUPPLIER"].reason_codes == ["SUPPLIER_MANAGEMENT_PERMISSION_REQUIRED"]
    assert len({decision.policy_version for decision in decisions.values()}) == 1


def test_unreachable_opa_fails_closed() -> None:
    with pytest.raises(PolicyServiceError):
        OPAClient("http://127.0.0.1:9", timeout_seconds=0.5).evaluate(policy_input())
