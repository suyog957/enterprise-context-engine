package procurement_test

import rego.v1

base_input := {
  "action": "CREATE_PURCHASE_ORDER",
  "principal": {
    "principal_id": "user-alice",
    "roles": ["BUYER"],
    "business_unit_ids": ["BU-000"],
    "approval_limit_minor": 1000000,
  },
  "requisition": {
    "requisition_id": "PR-1007",
    "business_unit_id": "BU-000",
    "state": "APPROVED",
    "amount_minor": 800000,
    "categories": ["Software"],
  },
  "supplier": {
    "canonical_entity_id": "supplier-acme",
    "status": "ACTIVE",
    "risk_rating": "LOW",
    "approved_categories": ["Software"],
  },
  "manager_approval_exists": false,
}

test_active_approved_requisition_is_allowed if {
  decision := data.procurement.decision with input as base_input
  decision.allowed
  not decision.approval_required
  decision.reason_codes == []
}

test_blocked_supplier_is_denied if {
  blocked_supplier := object.union(base_input.supplier, {"status": "BLOCKED"})
  test_input := object.union(base_input, {"supplier": blocked_supplier})
  decision := data.procurement.decision with input as test_input
  not decision.allowed
  "SUPPLIER_BLOCKED" in decision.reason_codes
}

test_over_limit_purchase_requires_manager_approval if {
  large_requisition := object.union(base_input.requisition, {"amount_minor": 1500000})
  test_input := object.union(base_input, {"requisition": large_requisition})
  decision := data.procurement.decision with input as test_input
  not decision.allowed
  decision.approval_required
  "MANAGER_APPROVAL_REQUIRED" in decision.reason_codes
}

test_business_unit_mismatch_is_denied if {
  other_requisition := object.union(base_input.requisition, {"business_unit_id": "BU-009"})
  test_input := object.union(base_input, {"requisition": other_requisition})
  decision := data.procurement.decision with input as test_input
  not decision.allowed
  "BUSINESS_UNIT_MISMATCH" in decision.reason_codes
}

test_blocked_supplier_cites_rule_sup_004 if {
  blocked_supplier := object.union(base_input.supplier, {"status": "BLOCKED"})
  test_input := object.union(base_input, {"supplier": blocked_supplier})
  decision := data.procurement.decision with input as test_input
  some rule in decision.policy_rules
  rule.rule_id == "SUP-004"
}

test_action_catalog_for_approved_requisition if {
  decisions := data.procurement.action_decisions with input as base_input
  decisions.CREATE_PURCHASE_ORDER.allowed
  decisions.CANCEL_REQUISITION.allowed
  not decisions.APPROVE_REQUISITION.allowed
  "STATE_NOT_ELIGIBLE" in decisions.APPROVE_REQUISITION.reason_codes
  not decisions.EDIT_SUPPLIER.allowed
  decisions.EDIT_SUPPLIER.reason_codes == ["SUPPLIER_MANAGEMENT_PERMISSION_REQUIRED"]
}

test_manager_can_approve_submitted_requisition_in_scope if {
  submitted := object.union(base_input.requisition, {"state": "SUBMITTED"})
  manager := object.union(base_input.principal, {"roles": ["BUYER", "MANAGER"]})
  test_input := object.union(base_input, {"requisition": submitted, "principal": manager})
  decisions := data.procurement.action_decisions with input as test_input
  decisions.APPROVE_REQUISITION.allowed
  decisions.SUBMIT_REQUISITION.reason_codes == ["STATE_NOT_ELIGIBLE"]
}

test_buyer_cannot_approve_and_out_of_scope_is_blocked if {
  submitted := object.union(base_input.requisition, {"state": "SUBMITTED", "business_unit_id": "BU-009"})
  test_input := object.union(base_input, {"requisition": submitted})
  decisions := data.procurement.action_decisions with input as test_input
  decisions.APPROVE_REQUISITION.reason_codes == ["BUSINESS_UNIT_MISMATCH", "ROLE_NOT_PERMITTED"]
}

test_blocked_requisition_over_limit_is_not_presented_as_approval_required if {
  draft := object.union(base_input.requisition, {"state": "SUBMITTED", "amount_minor": 5000000})
  test_input := object.union(base_input, {"requisition": draft})
  decision := data.procurement.decision with input as test_input
  not decision.allowed
  not decision.approval_required
  decision.reason_codes == ["REQUISITION_NOT_APPROVED"]
}

test_manager_approval_allows_without_pending_approval_flag if {
  over_limit := object.union(base_input.requisition, {"amount_minor": 5000000})
  test_input := object.union(base_input, {"requisition": over_limit, "manager_approval_exists": true})
  decision := data.procurement.decision with input as test_input
  decision.allowed
  not decision.approval_required
}
