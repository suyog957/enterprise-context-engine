package authorization

import rego.v1

has_write_role(principal) if {
  some role in principal.roles
  role in {"BUYER", "MANAGER", "ADMIN"}
}

in_business_unit(principal, resource) if {
  resource.business_unit_id in principal.business_unit_ids
}
