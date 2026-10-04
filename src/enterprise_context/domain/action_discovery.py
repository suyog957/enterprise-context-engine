"""Action catalog and allowed-action discovery.

The catalog defines candidate actions and their semantics; OPA decides whether a
specific principal may perform each one against current facts. Discovery and
execution use the same policy bundle and input, so the agent learns the valid action
space before planning instead of from failed API calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

from enterprise_context.domain.requisitions import RequisitionContext
from enterprise_context.policy.models import PolicyDecision, PolicyRuleRef

ActionStatus = Literal["AVAILABLE", "APPROVAL_REQUIRED", "BLOCKED"]


class ActionDefinition(BaseModel):
    action: str
    label: str
    required_entity_type: str = "PurchaseRequisition"
    preconditions: list[str]
    effects: list[str]
    required_permissions: list[str]
    executable: bool = Field(
        description="Whether this release exposes an execution endpoint for the action."
    )
    execution_endpoint: str | None = None


ACTION_CATALOG: tuple[ActionDefinition, ...] = (
    ActionDefinition(
        action="CREATE_PURCHASE_ORDER",
        label="Create purchase order",
        preconditions=[
            "PurchaseRequisition.state = APPROVED",
            "Supplier.status = ACTIVE",
            "Principal is authorized for the requisition's business unit",
            "Requisition categories are approved for the supplier",
            "Amount <= approval limit, supplier risk is not HIGH and category does not "
            "require review - otherwise manager approval must exist",
        ],
        effects=["PurchaseOrder created", "Requisition transitions to CONVERTED"],
        required_permissions=["BUYER", "MANAGER", "ADMIN"],
        executable=True,
        execution_endpoint="POST /requisitions/{id}/create-po",
    ),
    ActionDefinition(
        action="SUBMIT_REQUISITION",
        label="Submit requisition",
        preconditions=["PurchaseRequisition.state = DRAFT", "Principal in business unit"],
        effects=["Requisition transitions to SUBMITTED"],
        required_permissions=["BUYER", "MANAGER", "ADMIN"],
        executable=False,
    ),
    ActionDefinition(
        action="APPROVE_REQUISITION",
        label="Approve requisition",
        preconditions=["PurchaseRequisition.state = SUBMITTED", "Principal in business unit"],
        effects=["Requisition transitions to APPROVED"],
        required_permissions=["MANAGER", "ADMIN"],
        executable=False,
    ),
    ActionDefinition(
        action="REJECT_REQUISITION",
        label="Reject requisition",
        preconditions=["PurchaseRequisition.state = SUBMITTED", "Principal in business unit"],
        effects=["Requisition transitions to REJECTED"],
        required_permissions=["MANAGER", "ADMIN"],
        executable=False,
    ),
    ActionDefinition(
        action="CANCEL_REQUISITION",
        label="Cancel requisition",
        preconditions=[
            "PurchaseRequisition.state in {DRAFT, SUBMITTED, APPROVED}",
            "Principal in business unit",
        ],
        effects=["Requisition transitions to CLOSED"],
        required_permissions=["BUYER", "MANAGER", "ADMIN"],
        executable=False,
    ),
    ActionDefinition(
        action="EDIT_SUPPLIER",
        label="Edit supplier master data",
        required_entity_type="Supplier",
        preconditions=["Principal has supplier-management permission"],
        effects=["Supplier master record updated"],
        required_permissions=["ADMIN"],
        executable=False,
    ),
)
CATALOG_BY_ACTION = {definition.action: definition for definition in ACTION_CATALOG}


class ActionAvailability(BaseModel):
    action: str
    status: ActionStatus
    reason_codes: list[str]
    explanations: list[str]
    label: str | None = None
    executable: bool = False
    execution_endpoint: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    policy_rules: list[PolicyRuleRef] = Field(default_factory=list)


class AllowedActionsResponse(BaseModel):
    requisition_id: str
    available_actions: list[str]
    approval_required_actions: list[str] = Field(default_factory=list)
    actions: list[ActionAvailability]
    policy_version: str


def action_status(decision: PolicyDecision) -> ActionStatus:
    if decision.allowed:
        return "AVAILABLE"
    if decision.approval_required:
        return "APPROVAL_REQUIRED"
    return "BLOCKED"


def build_allowed_actions(
    context: RequisitionContext, decisions: Mapping[str, PolicyDecision]
) -> AllowedActionsResponse:
    """Combine catalog semantics with per-action OPA decisions, catalog order preserved."""
    actions: list[ActionAvailability] = []
    for definition in ACTION_CATALOG:
        decision = decisions.get(definition.action)
        if decision is None:
            continue
        actions.append(
            ActionAvailability(
                action=definition.action,
                status=action_status(decision),
                reason_codes=decision.reason_codes,
                explanations=decision.explanations,
                label=definition.label,
                executable=definition.executable,
                execution_endpoint=definition.execution_endpoint,
                preconditions=definition.preconditions,
                effects=definition.effects,
                required_permissions=definition.required_permissions,
                policy_rules=decision.policy_rules,
            )
        )
    versions = {decision.policy_version for decision in decisions.values()}
    return AllowedActionsResponse(
        requisition_id=context.requisition_id,
        available_actions=[item.action for item in actions if item.status == "AVAILABLE"],
        approval_required_actions=[
            item.action for item in actions if item.status == "APPROVAL_REQUIRED"
        ],
        actions=actions,
        policy_version=",".join(sorted(versions)) or "unknown",
    )
