import json
import logging
import re
import time
from typing import Annotated, Any
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from enterprise_context.agents.dependencies import get_agent_runs, get_agent_workflow
from enterprise_context.agents.workflow import (
    AgentExecutionError,
    AgentRequest,
    AgentResponse,
)
from enterprise_context.config import get_settings
from enterprise_context.context_engine.dependencies import get_context_engine
from enterprise_context.context_engine.models import ContextEnvelope
from enterprise_context.database import check_database
from enterprise_context.domain.action_discovery import (
    AllowedActionsResponse,
    build_allowed_actions,
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
from enterprise_context.graph.dependencies import get_graph_store
from enterprise_context.graph.nl2sparql import GraphAnswer, SparqlGenerationError
from enterprise_context.graph.query import (
    GraphQueryError,
    GraphQueryResult,
    GraphQueryValidationError,
)
from enterprise_context.graph.templates import (
    GraphTemplateError,
    GraphTemplateResult,
    GraphTemplateService,
)
from enterprise_context.observability.context import current_trace_id, set_request_id
from enterprise_context.observability.jaeger import fetch_trace_summary
from enterprise_context.observability.logging import configure_logging
from enterprise_context.observability.tracing import configure_tracing
from enterprise_context.policy.client import PolicyServiceError
from enterprise_context.retrieval.dependencies import get_search_retriever
from enterprise_context.retrieval.models import SearchRequest, SearchResponse
from enterprise_context.retrieval.opensearch import OpenSearchError
from enterprise_context.security.principals import PrincipalContext, get_current_principal
from enterprise_context.tools.dependencies import (
    get_graph_template_service,
    get_nl_graph_query,
    get_sql_catalog,
    get_tool_registry,
)
from enterprise_context.tools.sql_catalog import (
    SqlAuthorizationError,
    SqlCatalog,
    SqlCatalogError,
    SqlQueryResult,
)

settings = get_settings()
configure_logging(settings.log_level)
tracing_enabled = configure_tracing(
    service_name=settings.service_name,
    service_version=settings.service_version,
    environment=settings.environment,
    otlp_endpoint=settings.otel_exporter_otlp_endpoint,
)
logger = logging.getLogger("enterprise_context.api")
app = FastAPI(
    title="Enterprise Context Graph Agent API",
    version=settings.service_version,
    description="Local-first semantic context and policy-grounded procurement API.",
)
Instrumentator().instrument(app).expose(app, include_in_schema=False)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@app.middleware("http")
async def attach_request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    inbound = request.headers.get("x-request-id", "")
    request_id = inbound if _REQUEST_ID_PATTERN.match(inbound) else str(uuid4())
    set_request_id(request_id)
    trace.get_current_span().set_attribute("ecg.request_id", request_id)
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    trace_id = current_trace_id()
    if trace_id:
        response.headers["x-trace-id"] = trace_id
    if not request.url.path.startswith(("/health", "/metrics")):
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
    return response


if tracing_enabled:
    # Added last so it is the outermost middleware and its span covers the request.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics")


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
        decisions = get_procurement_transactions().evaluate_action_catalog(context, principal)
    except TransactionError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except PolicyServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="policy_service_unavailable",
        ) from error
    return build_allowed_actions(context, decisions)


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
        response = get_agent_workflow().invoke(request, principal)
    except AgentExecutionError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    get_agent_runs().save(response, principal.principal_id, request.question)
    return response


@app.get("/agent/runs/{run_id}", tags=["agent"])
def agent_run(
    run_id: str,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> dict[str, Any]:
    """A stored trajectory; principals see their own runs, admins and auditors see all."""
    if not _REQUEST_ID_PATTERN.match(run_id):
        raise HTTPException(status_code=400, detail="invalid_run_id")
    try:
        run = get_agent_runs().get(run_id)
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="database_unavailable") from error
    global_reader = bool({"ADMIN", "AUDITOR"}.intersection(principal.roles))
    if run is None or (not global_reader and run["principal_id"] != principal.principal_id):
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return run


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


class GraphTemplateRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class GraphQuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class AnalyticsRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


@app.post("/graph/query", response_model=GraphQueryResult, tags=["graph"])
def query_graph(
    request: GraphQueryRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> GraphQueryResult:
    """Raw read-only SPARQL. The RDF projection is not business-unit partitioned, so raw
    queries are limited to global readers; scoped access goes through templates."""
    if not {"ADMIN", "AUDITOR"}.intersection(principal.roles):
        raise HTTPException(status_code=403, detail="raw_graph_query_requires_global_read")
    try:
        return get_graph_store().run_readonly_sparql(request.query)
    except GraphQueryValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except GraphQueryError as error:
        raise HTTPException(status_code=503, detail="graph_service_unavailable") from error


@app.get("/graph/templates", tags=["graph"])
def list_graph_templates(
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> list[dict[str, Any]]:
    del principal
    return GraphTemplateService.describe()


@app.post(
    "/graph/templates/{template_name}", response_model=GraphTemplateResult, tags=["graph"]
)
def run_graph_template(
    template_name: str,
    request: GraphTemplateRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> GraphTemplateResult:
    if not {"BUYER", "MANAGER", "ADMIN", "AUDITOR"}.intersection(principal.roles):
        raise HTTPException(status_code=403, detail="graph_read_permission_required")
    try:
        return get_graph_template_service().run(template_name, request.parameters, principal)
    except (GraphTemplateError, GraphQueryValidationError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except GraphQueryError as error:
        raise HTTPException(status_code=503, detail="graph_service_unavailable") from error


@app.post("/graph/ask", response_model=GraphAnswer, tags=["graph"])
def ask_graph(
    request: GraphQuestionRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> GraphAnswer:
    """Natural-language graph question answered by validated, generated SPARQL."""
    if not {"BUYER", "MANAGER", "ADMIN", "AUDITOR"}.intersection(principal.roles):
        raise HTTPException(status_code=403, detail="graph_read_permission_required")
    try:
        return get_nl_graph_query().answer(request.question, principal)
    except SparqlGenerationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except GraphQueryError as error:
        raise HTTPException(status_code=503, detail="graph_service_unavailable") from error


@app.get("/analytics/queries", tags=["analytics"])
def list_analytics_queries(
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> list[dict[str, Any]]:
    del principal
    return SqlCatalog.describe()


@app.post("/analytics/{query_name}", response_model=SqlQueryResult, tags=["analytics"])
def run_analytics_query(
    query_name: str,
    request: AnalyticsRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> SqlQueryResult:
    try:
        return get_sql_catalog().run(query_name, request.parameters, principal)
    except SqlAuthorizationError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except SqlCatalogError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


class ContextRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@app.post("/context", response_model=ContextEnvelope, tags=["context"])
def build_context(
    request: ContextRequest,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> ContextEnvelope:
    """Authorized, routed context for a question without invoking the agent."""
    return get_context_engine().build(request.question, principal)


@app.get("/tools", tags=["agent"])
def list_tools(
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> list[dict[str, Any]]:
    del principal
    return get_tool_registry().specs()


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
    """Audit events plus the distributed-trace span summary for a request or trace ID."""
    if "ADMIN" not in principal.roles and "AUDITOR" not in principal.roles:
        raise HTTPException(status_code=403, detail="trace_read_permission_required")
    if not _REQUEST_ID_PATTERN.match(trace_id):
        raise HTTPException(status_code=400, detail="invalid_trace_id")
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
                      OR details ->> 'trace_id' = %s
                   ORDER BY occurred_at""",
                (trace_id, trace_id, trace_id),
            ).fetchall()
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="database_unavailable") from error
    events = [dict(row) for row in rows]
    jaeger_trace_id = next(
        (str(event["details"].get("trace_id")) for event in events
         if isinstance(event.get("details"), dict) and event["details"].get("trace_id")),
        trace_id,
    )
    try:
        run = get_agent_runs().get(trace_id)
    except psycopg.Error:
        run = None
    if run is not None and run.get("trace_id"):
        jaeger_trace_id = str(run["trace_id"])
    spans = fetch_trace_summary(settings.jaeger_query_url, jaeger_trace_id)
    return {
        "trace_id": jaeger_trace_id,
        "agent_run": run,
        "events": events,
        "spans": spans.spans,
        "span_source": spans.source,
        "jaeger_url": (
            f"{settings.jaeger_public_url.rstrip('/')}/trace/{jaeger_trace_id}"
            if spans.spans
            else None
        ),
    }


@app.get("/evaluation/latest", tags=["evaluation"])
def latest_evaluation(
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> dict[str, Any]:
    if "ADMIN" not in principal.roles and "AUDITOR" not in principal.roles:
        raise HTTPException(status_code=403, detail="evaluation_read_permission_required")
    report_dir = settings.data_dir / "evaluation" / "generated"
    report_path = next(
        (
            report_dir / name
            for name in ("latest.json", "smoke_latest.json", "policy_latest.json")
            if (report_dir / name).exists()
        ),
        None,
    )
    if report_path is None:
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
