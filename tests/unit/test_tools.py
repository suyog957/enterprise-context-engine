import time
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from enterprise_context.domain.entities import EntityResolveResponse
from enterprise_context.domain.requisitions import RequisitionContext, SupplierContext
from enterprise_context.domain.write_models import ActionSimulation
from enterprise_context.graph.templates import GraphTemplateService
from enterprise_context.policy.client import PolicyServiceError
from enterprise_context.policy.models import PolicyDecision, PolicyRuleRef
from enterprise_context.retrieval.models import SearchHit, SearchResponse
from enterprise_context.security.principals import PrincipalContext
from enterprise_context.tools.base import Tool, ToolContext, ToolRegistry, ToolStatus
from enterprise_context.tools.catalog import ToolServices, build_tools
from enterprise_context.tools.sql_catalog import SqlCatalog

PRINCIPAL = PrincipalContext(
    principal_id="user-alice",
    display_name="Alice",
    buyer_id="BUY-000",
    roles=["BUYER"],
    business_unit_ids=["BU-000"],
    approval_limit_minor=1_000_000,
)
CONTEXT = ToolContext(principal=PRINCIPAL)


def requisition(requisition_id: str = "PR-1007") -> RequisitionContext:
    return RequisitionContext(
        requisition_id=requisition_id,
        supplier_source_id="SUP-0000",
        canonical_supplier_id="supplier-acme",
        buyer_id="BUY-000",
        buyer_name="Alice",
        business_unit_id="BU-000",
        state="APPROVED",
        amount=Decimal("8000.00"),
        currency="USD",
        product_ids=["PROD-1"],
        categories=["Software"],
        supplier=SupplierContext(
            canonical_entity_id="supplier-acme",
            preferred_name="Acme Corp",
            status="ACTIVE",
            risk_rating="LOW",
            approved_categories=["Software"],
        ),
        active_contract_ids=[],
        row_version=1,
    )


def decision(**overrides: Any) -> PolicyDecision:
    values: dict[str, Any] = {
        "allowed": True,
        "approval_required": False,
        "reason_codes": [],
        "explanations": [],
        "policy_version": "0.2.0",
    }
    values.update(overrides)
    return PolicyDecision(**values)


def services(**overrides: Any) -> ToolServices:
    def simulate(requisition_id: str, principal: PrincipalContext) -> ActionSimulation:
        return ActionSimulation(
            requisition_id=requisition_id, decision=decision(), available=True,
            approval_required=False,
        )

    values: dict[str, Any] = {
        "resolve_entities": lambda request, principal: EntityResolveResponse(
            query=request.query, candidates=[]
        ),
        "search": lambda request, principal: SearchResponse(query=request.query, hits=[]),
        "entity_context": lambda entity_id, principal: None,
        "requisition_context": lambda requisition_id, principal: (
            requisition(requisition_id) if requisition_id == "PR-1007" else None
        ),
        "action_decisions": lambda context, principal: {"CREATE_PURCHASE_ORDER": decision()},
        "simulate": simulate,
        "graph_templates": GraphTemplateService(store=None),  # type: ignore[arg-type]
        "sql": SqlCatalog(lambda: None),  # type: ignore[arg-type,return-value]
    }
    values.update(overrides)
    return ToolServices(**values)


def registry(**overrides: Any) -> ToolRegistry:
    return ToolRegistry(build_tools(services(**overrides)))


def test_arguments_are_validated_before_any_service_runs() -> None:
    result = registry().invoke("get_allowed_actions", {"requisition_id": "DROP TABLE"}, CONTEXT)

    assert result.status is ToolStatus.INVALID
    assert result.attempts == 0


def test_tools_outside_the_allowlist_are_refused() -> None:
    result = registry().invoke(
        "simulate_create_purchase_order",
        {"requisition_id": "PR-1007"},
        CONTEXT,
        allowed={"get_allowed_actions"},
    )
    unknown = registry().invoke("run_shell", {}, CONTEXT)

    assert result.status is ToolStatus.NOT_ALLOWED
    assert unknown.status is ToolStatus.NOT_ALLOWED


def test_out_of_scope_resources_are_reported_as_not_found() -> None:
    result = registry().invoke("get_allowed_actions", {"requisition_id": "PR-9999"}, CONTEXT)

    assert result.status is ToolStatus.NOT_FOUND


def test_transient_failures_are_retried_once_then_reported_unavailable() -> None:
    calls: list[int] = []

    def failing(context: RequisitionContext, principal: PrincipalContext) -> Any:
        calls.append(1)
        raise PolicyServiceError("down")

    result = registry(action_decisions=failing).invoke(
        "get_allowed_actions", {"requisition_id": "PR-1007"}, CONTEXT
    )

    assert result.status is ToolStatus.UNAVAILABLE
    assert result.attempts == 2
    assert len(calls) == 2


def test_slow_tools_time_out() -> None:
    class Empty(BaseModel):
        pass

    def slow(args: Empty, ctx: ToolContext) -> Empty:
        time.sleep(0.5)
        return Empty()

    result = ToolRegistry([Tool("slow", "sleeps", Empty, slow, timeout_seconds=0.05)]).invoke(
        "slow", {}, CONTEXT
    )

    assert result.status is ToolStatus.TIMEOUT


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (decision(), "CONFIRMATION_REQUIRED"),
        (
            decision(
                allowed=False, approval_required=True, reason_codes=["MANAGER_APPROVAL_REQUIRED"]
            ),
            "APPROVAL_REQUIRED",
        ),
        (decision(allowed=False, reason_codes=["SUPPLIER_BLOCKED"]), "NOT_PERMITTED"),
    ],
)
def test_create_purchase_order_tool_only_proposes(policy: PolicyDecision, expected: str) -> None:
    def must_not_simulate(requisition_id: str, principal: PrincipalContext) -> Any:
        raise AssertionError("proposal must not execute anything")

    result = registry(
        action_decisions=lambda context, principal: {"CREATE_PURCHASE_ORDER": policy},
        simulate=must_not_simulate,
    ).invoke("create_purchase_order", {"requisition_id": "PR-1007"}, CONTEXT)

    assert result.status is ToolStatus.OK
    assert result.output["status"] == expected
    assert result.output["requires_idempotency_key"] is True


def test_policy_explanation_cites_rules_and_matching_documents() -> None:
    blocked = decision(
        allowed=False,
        reason_codes=["SUPPLIER_BLOCKED"],
        explanations=["The supplier is blocked."],
        policy_rules=[
            PolicyRuleRef(rule_id="SUP-004", document_id="POL-000", reason_code="SUPPLIER_BLOCKED")
        ],
    )
    hit = SearchHit(
        document_id="POL-000", title="Supplier status controls", content="...",
        document_type="PROCUREMENT_POLICY", source_system="DOCS", source_record_id="DOC-000",
        rrf_score=0.03, rrf_rank=1,
    )
    result = registry(
        action_decisions=lambda context, principal: {"CREATE_PURCHASE_ORDER": blocked},
        search=lambda request, principal: SearchResponse(query=request.query, hits=[hit]),
    ).invoke("get_policy_explanation", {"requisition_id": "PR-1007"}, CONTEXT)

    assert result.output["cited_rules"][0]["rule_id"] == "SUP-004"
    assert result.output["supporting_documents"][0]["document_id"] == "POL-000"


def test_tool_specs_expose_schemas_and_write_intent() -> None:
    specs = {spec["name"]: spec for spec in registry().specs()}

    assert specs["create_purchase_order"]["read_only"] is False
    assert all(spec["read_only"] for name, spec in specs.items() if name != "create_purchase_order")
    assert "requisition_id" in specs["get_allowed_actions"]["parameters"]["properties"]
