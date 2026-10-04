from typing import Literal

from pydantic import BaseModel


class ActionAvailability(BaseModel):
    action: str
    status: Literal["AVAILABLE", "APPROVAL_REQUIRED", "BLOCKED"]
    reason_codes: list[str]
    explanations: list[str]


class AllowedActionsResponse(BaseModel):
    requisition_id: str
    available_actions: list[str]
    actions: list[ActionAvailability]
    policy_version: str
