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
