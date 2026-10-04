import json
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from enterprise_context.agents.dependencies import get_procurement_agent
from enterprise_context.agents.procurement import (
    AgentExecutionError,
    AgentRequest,
    AgentResponse,
)
from enterprise_context.config import get_settings
from enterprise_context.database import check_database
from enterprise_context.domain.action_responses import (
    ActionAvailability,
    AllowedActionsResponse,
)
from enterprise_context.domain.entities import (
    EntityContextResponse,
    EntityResolveRequest,
    EntityResolveResponse,
    get_entity_context,
    resolve_entities,
)
from enterprise_context.domain.requisitions import (
    RequisitionContext,
    get_authorized_requisition_context,
)
from enterprise_context.domain.transaction_dependencies import get_procurement_transactions
from enterprise_context.domain.transactions import TransactionError
from enterprise_context.domain.write_models import (
    ActionSimulation,
    ApprovalDecisionRequest,
    ApprovalRequestResult,
    PurchaseOrderCommand,
    PurchaseOrderExecution,
)
from enterprise_context.graph.query import (
    FusekiGraphStore,
    GraphQueryError,
    GraphQueryResult,
    GraphQueryValidationError,
)
from enterprise_context.policy.client import PolicyServiceError
from enterprise_context.policy.models import PolicyDecision
from enterprise_context.retrieval.dependencies import get_search_retriever
from enterprise_context.retrieval.models import SearchRequest, SearchResponse
from enterprise_context.retrieval.opensearch import OpenSearchError
from enterprise_context.security.principals import PrincipalContext, get_current_principal

settings = get_settings()
app = FastAPI(
    title="Enterprise Context Graph Agent API",
    version="0.1.0",
    description="Local-first semantic context and policy-grounded procurement API.",
)
Instrumentator().instrument(app).expose(app, include_in_schema=False)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("x-request-id") or str(uuid4())
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "alive", "service": settings.service_name}


@app.get("/health/ready", tags=["health"])
def ready() -> dict[str, str]:
    if not check_database():
        raise HTTPException(status_code=503, detail="database_unavailable")
    return {"status": "ready", "service": settings.service_name}


@app.get("/requisitions/{requisition_id}", response_model=RequisitionContext, tags=["requisitions"])
def requisition_details(
    requisition_id: str,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> RequisitionContext:
    context = get_authorized_requisition_context(requisition_id, principal)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="requisition_not_found")
    return context


@app.get(
    "/requisitions/{requisition_id}/allowed-actions",
    response_model=AllowedActionsResponse,
    tags=["requisitions"],
)
def allowed_requisition_actions(
    requisition_id: str,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AllowedActionsResponse:
    context = get_authorized_requisition_context(requisition_id, principal)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="requisition_not_found")

    try:
        decision: PolicyDecision = get_procurement_transactions().evaluate_action(
            context, principal
        )
    except TransactionError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except PolicyServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="policy_service_unavailable",
        ) from error

    if decision.allowed:
        action_status: Literal["AVAILABLE", "APPROVAL_REQUIRED", "BLOCKED"] = "AVAILABLE"
    elif decision.approval_required:
        action_status = "APPROVAL_REQUIRED"
    else:
        action_status = "BLOCKED"
    return AllowedActionsResponse(
        requisition_id=context.requisition_id,
        available_actions=["CREATE_PURCHASE_ORDER"] if decision.allowed else [],
        actions=[
            ActionAvailability(
                action="CREATE_PURCHASE_ORDER",
                status=action_status,
                reason_codes=decision.reason_codes,
                explanations=decision.explanations,
            )
        ],
        policy_version=decision.policy_version,
    )


@app.post("/entities/resolve", response_model=EntityResolveResponse, tags=["entities"])
def resolve_entity_candidates(
    request: EntityResolveRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> EntityResolveResponse:
    try:
        return resolve_entities(request, principal)
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="database_unavailable") from error


@app.get("/entities/{entity_id}", response_model=EntityContextResponse, tags=["entities"])
def entity_details(
    entity_id: str,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> EntityContextResponse:
    try:
        context = get_entity_context(entity_id, principal)
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="database_unavailable") from error
    if context is None:
        raise HTTPException(status_code=404, detail="entity_not_found")
    return context


@app.get("/entities/{entity_id}/context", response_model=EntityContextResponse, tags=["entities"])
def entity_context(
    entity_id: str,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> EntityContextResponse:
    return entity_details(entity_id, principal)


@app.post("/chat", response_model=AgentResponse, tags=["agent"])
def chat(
    request: AgentRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AgentResponse:
    try:
        return get_procurement_agent().invoke(request, principal)
    except AgentExecutionError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@app.post("/search", response_model=SearchResponse, tags=["search"])
def search_documents(
    request: SearchRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> SearchResponse:
    try:
        return get_search_retriever().search(
            request,
            allowed_roles=principal.roles,
            business_unit_ids=principal.business_unit_ids,
        )
    except OpenSearchError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="search_service_unavailable",
        ) from error


class GraphQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=5000)


@app.post("/graph/query", response_model=GraphQueryResult, tags=["graph"])
def query_graph(
    request: GraphQueryRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> GraphQueryResult:
    if not {"BUYER", "MANAGER", "ADMIN", "AUDITOR"}.intersection(principal.roles):
        raise HTTPException(status_code=403, detail="graph_read_permission_required")
    try:
        return FusekiGraphStore(settings.fuseki_url).run_readonly_sparql(request.query)
    except GraphQueryValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except GraphQueryError as error:
        raise HTTPException(status_code=503, detail="graph_service_unavailable") from error


@app.post(
    "/requisitions/{requisition_id}/simulate-po",
    response_model=ActionSimulation,
    tags=["requisitions"],
)
def simulate_purchase_order(
    requisition_id: str,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> ActionSimulation:
    try:
        return get_procurement_transactions().simulate(requisition_id, principal)
    except TransactionError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except PolicyServiceError as error:
        raise HTTPException(status_code=503, detail="policy_service_unavailable") from error


@app.post(
    "/requisitions/{requisition_id}/approval-requests",
    response_model=ApprovalRequestResult,
    status_code=status.HTTP_201_CREATED,
    tags=["approvals"],
)
def request_purchase_approval(
    requisition_id: str,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> ApprovalRequestResult:
    try:
        return get_procurement_transactions().request_approval(requisition_id, principal)
    except TransactionError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except PolicyServiceError as error:
        raise HTTPException(status_code=503, detail="policy_service_unavailable") from error


@app.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalRequestResult,
    tags=["approvals"],
)
def decide_purchase_approval(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> ApprovalRequestResult:
    try:
        return get_procurement_transactions().decide_approval(
            approval_id, principal, request
        )
    except TransactionError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@app.post(
    "/requisitions/{requisition_id}/create-po",
    response_model=PurchaseOrderExecution,
    tags=["requisitions"],
)
def create_purchase_order(
    requisition_id: str,
    command: PurchaseOrderCommand,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> PurchaseOrderExecution:
    try:
        return get_procurement_transactions().create_purchase_order(
            requisition_id, principal, idempotency_key, command
        )
    except TransactionError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except PolicyServiceError as error:
        raise HTTPException(status_code=503, detail="policy_service_unavailable") from error


@app.get("/traces/{trace_id}", tags=["observability"])
def trace_details(
    trace_id: str,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> dict[str, Any]:
    if "ADMIN" not in principal.roles and "AUDITOR" not in principal.roles:
        raise HTTPException(status_code=403, detail="trace_read_permission_required")
    try:
        with psycopg.connect(
            settings.database_url, connect_timeout=3, row_factory=dict_row
        ) as connection:
            rows = connection.execute(
                """SELECT event_id, request_id, actor_id, action_type, resource_type,
                          resource_id, outcome, reason_codes, policy_version,
                          details, occurred_at
                   FROM audit_event
                   WHERE request_id = %s OR event_id::text = %s
                   ORDER BY occurred_at""",
                (trace_id, trace_id),
            ).fetchall()
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="database_unavailable") from error
    return {"trace_id": trace_id, "events": [dict(row) for row in rows]}


@app.get("/evaluation/latest", tags=["evaluation"])
def latest_evaluation(
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> dict[str, Any]:
    if "ADMIN" not in principal.roles and "AUDITOR" not in principal.roles:
        raise HTTPException(status_code=403, detail="evaluation_read_permission_required")
    report_path = settings.data_dir / "evaluation" / "generated" / "policy_latest.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="evaluation_report_not_found")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=500, detail="evaluation_report_unreadable") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="evaluation_report_unreadable")
    return payload


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exception: Exception) -> JSONResponse:
    del request, exception
    return JSONResponse(status_code=500, content={"error": "internal_error"})
