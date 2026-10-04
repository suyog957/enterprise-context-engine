package procurement

import rego.v1

policy_version := "0.2.0"

default decision := {
  "allowed": false,
  "approval_required": false,
  "reason_codes": ["POLICY_INPUT_INVALID"],
  "explanations": ["Required policy input is missing or invalid."],
  "policy_rules": [],
  "policy_version": "0.2.0",
}

decision := {
  "allowed": allowed,
  "approval_required": approval_required,
  "reason_codes": reason_codes,
  "explanations": explanations,
  "policy_rules": [rule_refs[code] | some code in reason_codes; rule_refs[code]],
  "policy_version": policy_version,
} if {
  is_number(input.requisition.amount_minor)
  is_string(input.requisition.state)
  is_string(input.requisition.business_unit_id)
  is_array(input.requisition.categories)
  is_number(input.principal.approval_limit_minor)
  is_array(input.principal.roles)
  is_array(input.principal.business_unit_ids)
  is_string(input.supplier.status)
  is_string(input.supplier.risk_rating)
  is_array(input.supplier.approved_categories)
  is_boolean(input.manager_approval_exists)
}

blocker_codes contains "ACTION_NOT_SUPPORTED" if {
  input.action != "CREATE_PURCHASE_ORDER"
}

blocker_codes contains "REQUISITION_NOT_APPROVED" if {
  input.requisition.state != "APPROVED"
}

blocker_codes contains "SUPPLIER_BLOCKED" if {
  input.supplier.status == "BLOCKED"
}

blocker_codes contains "SUPPLIER_NOT_ACTIVE" if {
  input.supplier.status != "ACTIVE"
  input.supplier.status != "BLOCKED"
}

blocker_codes contains "USER_NOT_AUTHORIZED" if {
  not data.authorization.has_write_role(input.principal)
}

blocker_codes contains "BUSINESS_UNIT_MISMATCH" if {
  not data.authorization.in_business_unit(input.principal, input.requisition)
}

blocker_codes contains "CATEGORY_NOT_APPROVED" if {
  some category in input.requisition.categories
  not category in input.supplier.approved_categories
}

approval_required if {
  input.requisition.amount_minor > input.principal.approval_limit_minor
}

approval_required if {
  input.supplier.risk_rating == "HIGH"
}

approval_required if {
  some category in input.requisition.categories
  category == "Legal"
}

default approval_required := false

approval_codes := ["MANAGER_APPROVAL_REQUIRED"] if {
  approval_required
  not input.manager_approval_exists
} else := []

reason_codes := array.concat(sort([code | some code in blocker_codes]), approval_codes)

allowed if {
  count(blocker_codes) == 0
  not approval_required
}

allowed if {
  count(blocker_codes) == 0
  approval_required
  input.manager_approval_exists
}

default allowed := false

messages := {
  "ACTION_NOT_SUPPORTED": "This action is not supported by the procurement policy.",
  "REQUISITION_NOT_APPROVED": "Only an approved requisition can be converted.",
  "SUPPLIER_BLOCKED": "The supplier is blocked and cannot receive a purchase order.",
  "SUPPLIER_NOT_ACTIVE": "The supplier is not active.",
  "USER_NOT_AUTHORIZED": "The principal does not have a purchasing role.",
  "BUSINESS_UNIT_MISMATCH": "The principal is not authorized for this business unit.",
  "CATEGORY_NOT_APPROVED": "The supplier is not approved for a requisition category.",
  "MANAGER_APPROVAL_REQUIRED": "A manager must approve this purchase before execution.",
}

explanations := [messages[code] |
  code := reason_codes[_]
  messages[code]
]

# Policy rule identifiers and the policy documents that state them, so every reason
# code can be traced to a citable rule.
rule_refs := {
  "REQUISITION_NOT_APPROVED": {"rule_id": "REQ-001", "document_id": "POL-004", "reason_code": "REQUISITION_NOT_APPROVED"},
  "SUPPLIER_BLOCKED": {"rule_id": "SUP-004", "document_id": "POL-000", "reason_code": "SUPPLIER_BLOCKED"},
  "SUPPLIER_NOT_ACTIVE": {"rule_id": "SUP-003", "document_id": "POL-000", "reason_code": "SUPPLIER_NOT_ACTIVE"},
  "CATEGORY_NOT_APPROVED": {"rule_id": "CAT-002", "document_id": "POL-003", "reason_code": "CATEGORY_NOT_APPROVED"},
  "MANAGER_APPROVAL_REQUIRED": {"rule_id": "APR-001", "document_id": "POL-001", "reason_code": "MANAGER_APPROVAL_REQUIRED"},
  "USER_NOT_AUTHORIZED": {"rule_id": "AUTH-001", "document_id": "POL-001", "reason_code": "USER_NOT_AUTHORIZED"},
  "BUSINESS_UNIT_MISMATCH": {"rule_id": "AUTH-002", "document_id": "POL-001", "reason_code": "BUSINESS_UNIT_MISMATCH"},
  "STATE_NOT_ELIGIBLE": {"rule_id": "REQ-002", "document_id": "POL-004", "reason_code": "STATE_NOT_ELIGIBLE"},
  "ROLE_NOT_PERMITTED": {"rule_id": "AUTH-003", "document_id": "POL-001", "reason_code": "ROLE_NOT_PERMITTED"},
  "SUPPLIER_MANAGEMENT_PERMISSION_REQUIRED": {"rule_id": "AUTH-004", "document_id": "POL-000", "reason_code": "SUPPLIER_MANAGEMENT_PERMISSION_REQUIRED"},
}

# ---------------------------------------------------------------------------
# Action catalog: every candidate action for a requisition is evaluated against the
# same input, so action discovery and execution share one policy bundle.
# ---------------------------------------------------------------------------

lifecycle_actions := {
  "SUBMIT_REQUISITION": {"states": {"DRAFT"}, "roles": {"BUYER", "MANAGER", "ADMIN"}},
  "APPROVE_REQUISITION": {"states": {"SUBMITTED"}, "roles": {"MANAGER", "ADMIN"}},
  "REJECT_REQUISITION": {"states": {"SUBMITTED"}, "roles": {"MANAGER", "ADMIN"}},
  "CANCEL_REQUISITION": {"states": {"DRAFT", "SUBMITTED", "APPROVED"}, "roles": {"BUYER", "MANAGER", "ADMIN"}},
}

lifecycle_blockers(name) := codes if {
  rule := lifecycle_actions[name]
  codes := {code |
    some code in ["STATE_NOT_ELIGIBLE", "ROLE_NOT_PERMITTED", "BUSINESS_UNIT_MISMATCH"]
    lifecycle_violation(code, rule)
  }
}

lifecycle_violation("STATE_NOT_ELIGIBLE", rule) if not input.requisition.state in rule.states

lifecycle_violation("ROLE_NOT_PERMITTED", rule) if {
  count({role | some role in input.principal.roles; role in rule.roles}) == 0
}

lifecycle_violation("BUSINESS_UNIT_MISMATCH", _) if {
  not data.authorization.in_business_unit(input.principal, input.requisition)
}

action_decision(codes) := {
  "allowed": count(codes) == 0,
  "approval_required": false,
  "reason_codes": sorted_codes,
  "explanations": [action_messages[code] | some code in sorted_codes],
  "policy_rules": [rule_refs[code] | some code in sorted_codes],
  "policy_version": policy_version,
} if {
  sorted_codes := sort([code | some code in codes])
}

action_messages := object.union(messages, {
  "STATE_NOT_ELIGIBLE": "The requisition's current state does not permit this action.",
  "ROLE_NOT_PERMITTED": "The principal's roles do not permit this action.",
  "SUPPLIER_MANAGEMENT_PERMISSION_REQUIRED": "Editing supplier master data requires supplier-management (ADMIN) permission.",
})

edit_supplier_codes := set() if {
  "ADMIN" in input.principal.roles
} else := {"SUPPLIER_MANAGEMENT_PERMISSION_REQUIRED"}

action_decisions := object.union(
  {name: action_decision(lifecycle_blockers(name)) | some name, _ in lifecycle_actions},
  {
    "CREATE_PURCHASE_ORDER": decision,
    "EDIT_SUPPLIER": action_decision(edit_supplier_codes),
  },
)
