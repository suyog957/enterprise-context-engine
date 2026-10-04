from decimal import Decimal

from enterprise_context.domain.requisitions import RequisitionContext
from enterprise_context.policy.models import (
    PrincipalPolicyInput,
    ProcurementPolicyInput,
    RequisitionPolicyInput,
    SupplierPolicyInput,
)
from enterprise_context.security.principals import PrincipalContext


def build_create_po_policy_input(
    requisition: RequisitionContext,
    principal: PrincipalContext,
    *,
    manager_approval_exists: bool = False,
) -> ProcurementPolicyInput:
    amount_minor = int((requisition.amount * Decimal(100)).to_integral_exact())
    return ProcurementPolicyInput(
        action="CREATE_PURCHASE_ORDER",
        principal=PrincipalPolicyInput(
            principal_id=principal.principal_id,
            roles=principal.roles,
            business_unit_ids=principal.business_unit_ids,
            approval_limit_minor=principal.approval_limit_minor,
        ),
        requisition=RequisitionPolicyInput(
            requisition_id=requisition.requisition_id,
            business_unit_id=requisition.business_unit_id,
            state=requisition.state,
            amount_minor=amount_minor,
            categories=requisition.categories,
        ),
        supplier=SupplierPolicyInput(
            canonical_entity_id=requisition.canonical_supplier_id or "unresolved",
            status=requisition.supplier.status,
            risk_rating=requisition.supplier.risk_rating,
            approved_categories=requisition.supplier.approved_categories,
        ),
        manager_approval_exists=manager_approval_exists,
    )
