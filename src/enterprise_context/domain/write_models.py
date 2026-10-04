from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from enterprise_context.policy.models import PolicyDecision


class ActionSimulation(BaseModel):
    requisition_id: str
    action: Literal["CREATE_PURCHASE_ORDER"] = "CREATE_PURCHASE_ORDER"
    decision: PolicyDecision
    available: bool
    approval_required: bool
    dry_run: bool = True


class ApprovalRequestResult(BaseModel):
    approval_id: UUID
    requisition_id: str
    action: Literal["CREATE_PURCHASE_ORDER"] = "CREATE_PURCHASE_ORDER"
    status: Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED", "INVALIDATED"]
    expires_at: str
    policy_version: str
    reason_codes: list[str]


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=1000)


class PurchaseOrderCommand(BaseModel):
    approval_id: UUID | None = None


class PurchaseOrderExecution(BaseModel):
    requisition_id: str
    purchase_order_id: str | None = None
    status: Literal["SIMULATED", "DENIED", "APPROVAL_REQUIRED", "CREATED"]
    dry_run: bool
    decision: PolicyDecision
    idempotent_replay: bool = False
