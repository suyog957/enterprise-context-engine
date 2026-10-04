from __future__ import annotations

from enterprise_context.policy.models import (
    PolicyDecision,
    PolicyRuleRef,
    ProcurementPolicyInput,
)

POLICY_VERSION = "0.2.1"

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


_RULES = {
    "REQUISITION_NOT_APPROVED": ("REQ-001", "POL-004"),
    "SUPPLIER_BLOCKED": ("SUP-004", "POL-000"),
    "SUPPLIER_NOT_ACTIVE": ("SUP-003", "POL-000"),
    "CATEGORY_NOT_APPROVED": ("CAT-002", "POL-003"),
    "MANAGER_APPROVAL_REQUIRED": ("APR-001", "POL-001"),
    "USER_NOT_AUTHORIZED": ("AUTH-001", "POL-001"),
    "BUSINESS_UNIT_MISMATCH": ("AUTH-002", "POL-001"),
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
    # Approval is pending only when nothing else blocks the action (mirrors Rego).
    approval_pending = (
        not blockers and approval_required and not policy_input.manager_approval_exists
    )
    reason_codes = sorted(blockers)
    if approval_pending:
        reason_codes.append("MANAGER_APPROVAL_REQUIRED")

    allowed = not blockers and (not approval_required or policy_input.manager_approval_exists)
    return PolicyDecision(
        allowed=allowed,
        approval_required=approval_pending,
        reason_codes=reason_codes,
        explanations=[_MESSAGES[code] for code in reason_codes],
        policy_version=POLICY_VERSION,
        policy_rules=[
            PolicyRuleRef(rule_id=_RULES[code][0], document_id=_RULES[code][1], reason_code=code)
            for code in reason_codes
        ],
    )
