package procurement

import rego.v1

policy_version := "0.1.0"

default decision := {
  "allowed": false,
  "approval_required": false,
  "reason_codes": ["POLICY_INPUT_INVALID"],
  "explanations": ["Required policy input is missing or invalid."],
  "policy_version": "0.1.0",
}

decision := {
  "allowed": allowed,
  "approval_required": approval_required,
  "reason_codes": reason_codes,
  "explanations": explanations,
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
