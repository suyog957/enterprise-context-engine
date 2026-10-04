from decimal import Decimal

import pytest

from enterprise_context.agents.procurement import (
    AgentExecutionError,
    AgentRequest,
    ProcurementAgent,
)
from enterprise_context.domain.requisitions import RequisitionContext, SupplierContext
from enterprise_context.policy.models import PolicyDecision
from enterprise_context.security.principals import PrincipalContext


def make_context() -> RequisitionContext:
    return RequisitionContext(
        requisition_id="PR-1007",
        supplier_source_id="SUP-0000",
        canonical_supplier_id="supplier-acme",
        buyer_id="BUY-000",
        buyer_name="Alice Morgan",
        business_unit_id="BU-000",
        state="APPROVED",
        amount=Decimal("8000.00"),
        currency="USD",
        product_ids=["PROD-00001"],
        categories=["Software"],
        supplier=SupplierContext(
            canonical_entity_id="supplier-acme",
            preferred_name="Acme Corp",
            status="ACTIVE",
            risk_rating="LOW",
            approved_categories=["Software"],
        ),
        active_contract_ids=["CON-2001"],
        row_version=1,
    )


def make_principal() -> PrincipalContext:
    return PrincipalContext(
        principal_id="user-alice",
        display_name="Alice Morgan",
        buyer_id="BUY-000",
        roles=["BUYER"],
        business_unit_ids=["BU-000"],
        approval_limit_minor=1_000_000,
    )


def test_langgraph_workflow_discovers_action_before_answering() -> None:
    context = make_context()
    observed: list[str] = []

    def load_context(requisition_id: str, principal: PrincipalContext) -> RequisitionContext:
        observed.append(f"context:{requisition_id}:{principal.principal_id}")
        return context

    def evaluate_policy(
        requisition: RequisitionContext, principal: PrincipalContext
    ) -> PolicyDecision:
        observed.append(f"policy:{requisition.requisition_id}:{principal.principal_id}")
        return PolicyDecision(
            allowed=True,
            approval_required=False,
            reason_codes=[],
            explanations=[],
            policy_version="test-v1",
        )

    agent = ProcurementAgent(load_context, evaluate_policy)
    response = agent.invoke(
        AgentRequest(question="Can PR-1007 be converted into a purchase order?"),
        make_principal(),
    )

    assert response.requisition_id == "PR-1007"
    assert response.available_actions == ["CREATE_PURCHASE_ORDER"]
    assert response.policy_decision.allowed
    assert "eligible" in response.answer
    assert "GRAPH_CONTEXT_UNAVAILABLE" in response.context_warnings
    assert "Graph context was unavailable" in response.answer
    assert observed == ["context:PR-1007:user-alice", "policy:PR-1007:user-alice"]
    assert [step.node for step in response.trajectory] == [
        "receive_request",
        "resolve_entities",
        "retrieve_context",
        "determine_allowed_actions",
        "create_plan",
        "verify_result",
        "generate_response",
    ]


def test_blocked_policy_never_exposes_the_create_action() -> None:
    def evaluate_policy(
        requisition: RequisitionContext, principal: PrincipalContext
    ) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            approval_required=False,
            reason_codes=["SUPPLIER_BLOCKED"],
            explanations=["The supplier is blocked."],
            policy_version="test-v1",
        )

    agent = ProcurementAgent(lambda requisition_id, principal: make_context(), evaluate_policy)
    response = agent.invoke(AgentRequest(question="Why is PR1007 blocked?"), make_principal())

    assert response.available_actions == []
    assert response.trajectory[4].detail == "Selected bounded plan: explain_block"
    assert "supplier is blocked" in response.answer


def test_agent_refuses_context_outside_the_principal_scope() -> None:
    agent = ProcurementAgent(
        lambda requisition_id, principal: None,
        lambda requisition, principal: PolicyDecision(
            allowed=True,
            approval_required=False,
            reason_codes=[],
            explanations=[],
            policy_version="test-v1",
        ),
    )

    with pytest.raises(AgentExecutionError, match="outside principal scope"):
        agent.invoke(AgentRequest(question="Can PR-1007 become a PO?"), make_principal())


def test_agent_rejects_questions_without_requisition_identity() -> None:
    agent = ProcurementAgent(
        lambda requisition_id, principal: make_context(),
        lambda requisition, principal: PolicyDecision(
            allowed=False,
            approval_required=False,
            reason_codes=["REQUISITION_NOT_APPROVED"],
            explanations=["The requisition is not approved."],
            policy_version="test-v1",
        ),
    )

    with pytest.raises(AgentExecutionError, match="identifier is required"):
        agent.invoke(AgentRequest(question="What is our purchasing policy?"), make_principal())
