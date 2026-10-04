from __future__ import annotations

from enterprise_context.policy.models import PolicyDecision, ProcurementPolicyInput

POLICY_VERSION = "0.1.0"

_MESSAGES = {
    "ACTION_NOT_SUPPORTED": "This action is not supported by the procurement policy.",
    "REQUISITION_NOT_APPROVED": "Only an approved requisition can be converted.",
    "SUPPLIER_BLOCKED": "The supplier is blocked and cannot receive a purchase order.",
    "SUPPLIER_NOT_ACTIVE": "The supplier is not active.",
    "USER_NOT_AUTHORIZED": "The principal does not have a purchasing role.",
    "BUSINESS_UNIT_MISMATCH": "The principal is not authorized for this business unit.",
    "CATEGORY_NOT_APPROVED": "The supplier is not approved for a requisition category.",
    "MANAGER_APPROVAL_REQUIRED": "A manager must approve this purchase before execution.",
}


def evaluate_procurement_policy(policy_input: ProcurementPolicyInput) -> PolicyDecision:
    """Evaluate the procurement Rego policy semantics without a live OPA server.

    This is intentionally used for deterministic smoke evaluation only. Protected
    application writes continue to use OPA through the policy client and fail closed.
    """
    blockers: set[str] = set()
    if policy_input.action != "CREATE_PURCHASE_ORDER":
        blockers.add("ACTION_NOT_SUPPORTED")
    if policy_input.requisition.state != "APPROVED":
        blockers.add("REQUISITION_NOT_APPROVED")
    if policy_input.supplier.status == "BLOCKED":
        blockers.add("SUPPLIER_BLOCKED")
    if policy_input.supplier.status not in {"ACTIVE", "BLOCKED"}:
        blockers.add("SUPPLIER_NOT_ACTIVE")
    if not set(policy_input.principal.roles).intersection({"BUYER", "MANAGER", "ADMIN"}):
        blockers.add("USER_NOT_AUTHORIZED")
    if policy_input.requisition.business_unit_id not in policy_input.principal.business_unit_ids:
        blockers.add("BUSINESS_UNIT_MISMATCH")
    approved_categories = set(policy_input.supplier.approved_categories)
    if any(category not in approved_categories for category in policy_input.requisition.categories):
        blockers.add("CATEGORY_NOT_APPROVED")

    approval_required = (
        policy_input.requisition.amount_minor > policy_input.principal.approval_limit_minor
        or policy_input.supplier.risk_rating == "HIGH"
        or "Legal" in policy_input.requisition.categories
    )
    reason_codes = sorted(blockers)
    if approval_required and not policy_input.manager_approval_exists:
        reason_codes.append("MANAGER_APPROVAL_REQUIRED")

    allowed = not blockers and (not approval_required or policy_input.manager_approval_exists)
    return PolicyDecision(
        allowed=allowed,
        approval_required=approval_required and not policy_input.manager_approval_exists,
        reason_codes=reason_codes,
        explanations=[_MESSAGES[code] for code in reason_codes],
        policy_version=POLICY_VERSION,
    )
