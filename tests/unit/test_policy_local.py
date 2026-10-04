from enterprise_context.policy.local import evaluate_procurement_policy
from enterprise_context.policy.models import (
    PrincipalPolicyInput,
    ProcurementPolicyInput,
    RequisitionPolicyInput,
    SupplierPolicyInput,
)


def policy_input(
    *,
    state: str = "APPROVED",
    status: str = "ACTIVE",
    amount_minor: int = 800_000,
    approval_limit_minor: int = 1_000_000,
    categories: list[str] | None = None,
    approved_categories: list[str] | None = None,
    manager_approval_exists: bool = False,
) -> ProcurementPolicyInput:
    return ProcurementPolicyInput(
        action="CREATE_PURCHASE_ORDER",
        principal=PrincipalPolicyInput(
            principal_id="user-alice",
            roles=["BUYER"],
            business_unit_ids=["BU-000"],
            approval_limit_minor=approval_limit_minor,
        ),
        requisition=RequisitionPolicyInput(
            requisition_id="PR-1007",
            business_unit_id="BU-000",
            state=state,
            amount_minor=amount_minor,
            categories=categories or ["Software"],
        ),
        supplier=SupplierPolicyInput(
            canonical_entity_id="supplier-acme",
            status=status,
            risk_rating="LOW",
            approved_categories=approved_categories or ["Software"],
        ),
        manager_approval_exists=manager_approval_exists,
    )


def test_local_policy_allows_clean_purchase_order_case() -> None:
    decision = evaluate_procurement_policy(policy_input())

    assert decision.allowed
    assert not decision.approval_required
    assert decision.reason_codes == []


def test_local_policy_blocks_supplier_status_like_rego() -> None:
    decision = evaluate_procurement_policy(policy_input(status="BLOCKED"))

    assert not decision.allowed
    assert decision.reason_codes == ["SUPPLIER_BLOCKED"]


def test_local_policy_requires_manager_approval_then_allows_after_resume() -> None:
    decision = evaluate_procurement_policy(
        policy_input(amount_minor=1_800_000, approval_limit_minor=1_000_000)
    )
    approved = evaluate_procurement_policy(
        policy_input(
            amount_minor=1_800_000,
            approval_limit_minor=1_000_000,
            manager_approval_exists=True,
        )
    )

    assert not decision.allowed
    assert decision.approval_required
    assert decision.reason_codes == ["MANAGER_APPROVAL_REQUIRED"]
    assert approved.allowed
    assert not approved.approval_required
