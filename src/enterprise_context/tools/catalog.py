"""Concrete agent tools.

Every tool is read-only except ``create_purchase_order``, which is write-*intent*: it
never writes. It returns a proposal that a human must confirm through the protected
``POST /requisitions/{id}/create-po`` endpoint, which re-checks authorization, policy,
approval and idempotency at execution time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from enterprise_context.domain.action_discovery import (
    AllowedActionsResponse,
    build_allowed_actions,
)
from enterprise_context.domain.entities import (
    EntityContextResponse,
    EntityResolveRequest,
    EntityResolveResponse,
)
from enterprise_context.domain.requisitions import RequisitionContext
from enterprise_context.domain.write_models import ActionSimulation
from enterprise_context.graph.nl2sparql import (
    GraphAnswer,
    NaturalLanguageGraphQuery,
    SparqlGenerationError,
)
from enterprise_context.graph.query import GraphQueryError, GraphQueryValidationError
from enterprise_context.graph.templates import (
    TEMPLATES,
    GraphTemplateError,
    GraphTemplateResult,
    GraphTemplateService,
)
from enterprise_context.policy.client import PolicyServiceError
from enterprise_context.policy.models import PolicyDecision, PolicyRuleRef
from enterprise_context.retrieval.models import SearchHit, SearchRequest, SearchResponse
from enterprise_context.retrieval.opensearch import OpenSearchError
from enterprise_context.security.principals import PrincipalContext
from enterprise_context.tools.base import Tool, ToolContext, ToolFailure, ToolStatus
from enterprise_context.tools.sql_catalog import (
    CATALOG,
    SqlAuthorizationError,
    SqlCatalog,
    SqlCatalogError,
    SqlQueryResult,
)

REQUISITION_ID = r"^PR-\d{4,8}$"
DocumentType = Literal["PROCUREMENT_POLICY", "CONTRACT"]


@dataclass(frozen=True)
class ToolServices:
    """Server-side capabilities the tools wrap; injected so tests can use fakes."""

    resolve_entities: Callable[[EntityResolveRequest, PrincipalContext], EntityResolveResponse]
    search: Callable[[SearchRequest, PrincipalContext], SearchResponse]
    entity_context: Callable[[str, PrincipalContext], EntityContextResponse | None]
    requisition_context: Callable[[str, PrincipalContext], RequisitionContext | None]
    action_decisions: Callable[[RequisitionContext, PrincipalContext], dict[str, PolicyDecision]]
    simulate: Callable[[str, PrincipalContext], ActionSimulation]
    graph_templates: GraphTemplateService
    sql: SqlCatalog
    nl_graph: NaturalLanguageGraphQuery | None = None
    dry_run: bool = True


class ResolveEntityInput(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=10)


class SearchDocumentsInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    document_types: list[DocumentType] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=10)


class QueryGraphInput(BaseModel):
    template: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("template")
    @classmethod
    def _known_template(cls, value: str) -> str:
        if value not in TEMPLATES:
            raise ValueError(f"Unknown graph template '{value}'")
        return value


class QueryGraphNaturalLanguageInput(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    entity_uris: list[str] = Field(default_factory=list, max_length=5)


class QuerySqlInput(BaseModel):
    query_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("query_name")
    @classmethod
    def _known_query(cls, value: str) -> str:
        if value not in CATALOG:
            raise ValueError(f"Unknown catalog query '{value}'")
        return value


class EntityIdInput(BaseModel):
    entity_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")


class RequisitionInput(BaseModel):
    requisition_id: str = Field(pattern=REQUISITION_ID)


class PolicyExplanation(BaseModel):
    requisition_id: str
    action: str = "CREATE_PURCHASE_ORDER"
    decision: PolicyDecision
    cited_rules: list[PolicyRuleRef]
    supporting_documents: list[SearchHit]


class PurchaseOrderProposal(BaseModel):
    requisition_id: str
    status: Literal["CONFIRMATION_REQUIRED", "APPROVAL_REQUIRED", "NOT_PERMITTED"]
    reason_codes: list[str]
    explanations: list[str]
    execution_endpoint: str = "POST /requisitions/{id}/create-po"
    approval_endpoint: str | None = None
    requires_idempotency_key: bool = True
    dry_run: bool
    note: str


def _require_requisition(
    services: ToolServices, requisition_id: str, principal: PrincipalContext
) -> RequisitionContext:
    context = services.requisition_context(requisition_id, principal)
    if context is None:
        raise ToolFailure(ToolStatus.NOT_FOUND, "Requisition not found or outside scope")
    return context


def build_tools(services: ToolServices) -> list[Tool]:
    def resolve_entity(args: ResolveEntityInput, ctx: ToolContext) -> EntityResolveResponse:
        return services.resolve_entities(
            EntityResolveRequest(query=args.query, limit=args.limit), ctx.principal
        )

    def search_documents(args: SearchDocumentsInput, ctx: ToolContext) -> SearchResponse:
        return services.search(
            SearchRequest(
                query=args.query, document_types=list(args.document_types), limit=args.limit
            ),
            ctx.principal,
        )

    def query_graph(args: QueryGraphInput, ctx: ToolContext) -> GraphTemplateResult:
        return services.graph_templates.run(args.template, args.parameters, ctx.principal)

    def query_graph_nl(args: QueryGraphNaturalLanguageInput, ctx: ToolContext) -> GraphAnswer:
        if services.nl_graph is None:
            raise ToolFailure(ToolStatus.UNAVAILABLE, "Natural-language graph querying is off")
        return services.nl_graph.answer(args.question, ctx.principal, args.entity_uris)

    def query_sql(args: QuerySqlInput, ctx: ToolContext) -> SqlQueryResult:
        return services.sql.run(args.query_name, args.parameters, ctx.principal)

    def get_entity_context(args: EntityIdInput, ctx: ToolContext) -> EntityContextResponse:
        context = services.entity_context(args.entity_id, ctx.principal)
        if context is None:
            raise ToolFailure(ToolStatus.NOT_FOUND, "Entity not found or outside scope")
        return context

    def get_requisition_context(args: RequisitionInput, ctx: ToolContext) -> RequisitionContext:
        return _require_requisition(services, args.requisition_id, ctx.principal)

    def get_allowed_actions(args: RequisitionInput, ctx: ToolContext) -> AllowedActionsResponse:
        context = _require_requisition(services, args.requisition_id, ctx.principal)
        return build_allowed_actions(context, services.action_decisions(context, ctx.principal))

    def simulate_create_purchase_order(
        args: RequisitionInput, ctx: ToolContext
    ) -> ActionSimulation:
        _require_requisition(services, args.requisition_id, ctx.principal)
        return services.simulate(args.requisition_id, ctx.principal)

    def get_policy_explanation(args: RequisitionInput, ctx: ToolContext) -> PolicyExplanation:
        context = _require_requisition(services, args.requisition_id, ctx.principal)
        decision = services.action_decisions(context, ctx.principal)["CREATE_PURCHASE_ORDER"]
        documents: list[SearchHit] = []
        if decision.explanations:
            try:
                hits = services.search(
                    SearchRequest(
                        query=" ".join(decision.explanations),
                        document_types=["PROCUREMENT_POLICY"],
                        limit=8,
                    ),
                    ctx.principal,
                ).hits
            except OpenSearchError:
                hits = []
            cited = {rule.document_id for rule in decision.policy_rules}
            documents = [hit for hit in hits if hit.document_id in cited] or hits[:2]
        return PolicyExplanation(
            requisition_id=context.requisition_id,
            decision=decision,
            cited_rules=decision.policy_rules,
            supporting_documents=documents,
        )

    def create_purchase_order(args: RequisitionInput, ctx: ToolContext) -> PurchaseOrderProposal:
        context = _require_requisition(services, args.requisition_id, ctx.principal)
        decision = services.action_decisions(context, ctx.principal)["CREATE_PURCHASE_ORDER"]
        if decision.allowed:
            status: Literal[
                "CONFIRMATION_REQUIRED", "APPROVAL_REQUIRED", "NOT_PERMITTED"
            ] = "CONFIRMATION_REQUIRED"
            note = "Eligible. A human must confirm; execution re-checks policy and idempotency."
        elif decision.approval_required:
            status = "APPROVAL_REQUIRED"
            note = "Manager approval is required before a purchase order can be created."
        else:
            status = "NOT_PERMITTED"
            note = "Policy does not permit this action; no proposal can be executed."
        return PurchaseOrderProposal(
            requisition_id=context.requisition_id,
            status=status,
            reason_codes=decision.reason_codes,
            explanations=decision.explanations,
            approval_endpoint=(
                "POST /requisitions/{id}/approval-requests"
                if status == "APPROVAL_REQUIRED"
                else None
            ),
            dry_run=services.dry_run,
            note=note,
        )

    graph_errors = {
        GraphTemplateError: ToolStatus.INVALID,
        GraphQueryValidationError: ToolStatus.INVALID,
        SparqlGenerationError: ToolStatus.INVALID,
    }
    return [
        Tool(
            "resolve_entity",
            "Resolve a supplier name, alias or identifier to authorized canonical entities "
            "with confidence bands.",
            ResolveEntityInput,
            resolve_entity,
        ),
        Tool(
            "search_documents",
            "Authorized hybrid (BM25 + vector + RRF) search over policies and contracts.",
            SearchDocumentsInput,
            search_documents,
            transient=(OpenSearchError,),
        ),
        Tool(
            "query_graph",
            "Run a named, parameterized SPARQL template over the current graph projection.",
            QueryGraphInput,
            query_graph,
            error_map=graph_errors,
            transient=(GraphQueryError,),
        ),
        Tool(
            "query_graph_nl",
            "Generate, validate and run a read-only SPARQL query when no template fits.",
            QueryGraphNaturalLanguageInput,
            query_graph_nl,
            timeout_seconds=40.0,
            error_map=graph_errors,
            transient=(GraphQueryError,),
        ),
        Tool(
            "query_sql",
            "Run a named query from the safe SQL catalog (business-unit scoped).",
            QuerySqlInput,
            query_sql,
            error_map={
                SqlAuthorizationError: ToolStatus.DENIED,
                SqlCatalogError: ToolStatus.INVALID,
            },
        ),
        Tool(
            "get_entity_context",
            "Canonical entity details, aliases, provenance and related records.",
            EntityIdInput,
            get_entity_context,
        ),
        Tool(
            "get_requisition_context",
            "Authorized requisition facts from PostgreSQL plus graph facts.",
            RequisitionInput,
            get_requisition_context,
        ),
        Tool(
            "get_allowed_actions",
            "Candidate actions with OPA decisions, reasons, preconditions and effects.",
            RequisitionInput,
            get_allowed_actions,
            transient=(PolicyServiceError,),
        ),
        Tool(
            "simulate_create_purchase_order",
            "Read-only simulation of purchase-order creation for a requisition.",
            RequisitionInput,
            simulate_create_purchase_order,
            transient=(PolicyServiceError,),
        ),
        Tool(
            "get_policy_explanation",
            "Policy decision for purchase-order creation with cited rules and documents.",
            RequisitionInput,
            get_policy_explanation,
            transient=(PolicyServiceError,),
        ),
        Tool(
            "create_purchase_order",
            "Propose purchase-order creation. Never writes: returns a proposal that needs "
            "human confirmation (and approval when required) via the protected endpoint.",
            RequisitionInput,
            create_purchase_order,
            read_only=False,
            transient=(PolicyServiceError,),
        ),
    ]
