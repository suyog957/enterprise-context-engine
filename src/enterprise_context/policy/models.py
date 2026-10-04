from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PrincipalPolicyInput(BaseModel):
    principal_id: str
    roles: list[str]
    business_unit_ids: list[str]
    approval_limit_minor: int = Field(ge=0)


class RequisitionPolicyInput(BaseModel):
    requisition_id: str
    business_unit_id: str
    state: str
    amount_minor: int = Field(ge=0)
    categories: list[str]


class SupplierPolicyInput(BaseModel):
    canonical_entity_id: str
    status: str
    risk_rating: str
    approved_categories: list[str]


class ProcurementPolicyInput(BaseModel):
    action: Literal["CREATE_PURCHASE_ORDER"]
    principal: PrincipalPolicyInput
    requisition: RequisitionPolicyInput
    supplier: SupplierPolicyInput
    manager_approval_exists: bool = False

    model_config = ConfigDict(extra="forbid")


class PolicyDecision(BaseModel):
    allowed: bool
    approval_required: bool
    reason_codes: list[str]
    explanations: list[str]
    policy_version: str
