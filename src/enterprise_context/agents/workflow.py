"""Bounded LangGraph agent workflow.

receive_request -> classify_intent -> resolve_entities -> retrieve_context ->
determine_allowed_actions -> create_plan -> validate_plan -> execute_tools ->
verify_result -> generate_response

The valid action space is discovered before planning, so the planner only ever sees
actions OPA currently allows (or that need approval). Plans may use only allowlisted,
read-only or write-intent tools; nothing here can write. Clarifications and failed
required context short-circuit straight to the response.
"""

from __future__ import annotations

import time
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from enterprise_context.agents.composer import AnswerComposer, Citation
from enterprise_context.context_engine.engine import ContextEngine, EntityResolution
from enterprise_context.context_engine.models import (
    Classification,
    Confidence,
    ContextEnvelope,
    Intent,
    PolicyResult,
    ResolvedEntity,
)
from enterprise_context.domain.action_discovery import ActionAvailability
from enterprise_context.observability.context import current_request_id, current_trace_id
from enterprise_context.observability.metrics import AGENT_RUNS, AGENT_STEPS
from enterprise_context.observability.tracing import traced
from enterprise_context.security.principals import PrincipalContext
from enterprise_context.tools.base import ToolContext, ToolInvocation, ToolRegistry

MAX_QUESTION_LENGTH = 2000
MAX_AGENT_STEPS = 14
MAX_PLAN_STEPS = 4
PLANNER_TOOLS = frozenset(
    {"get_policy_explanation", "simulate_create_purchase_order", "create_purchase_order"}
)
AgentStatus = Literal["ANSWERED", "PARTIAL", "NEEDS_CLARIFICATION", "UNAVAILABLE"]


class AgentRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)


class AgentTraceStep(BaseModel):
    node: str
    status: str
    detail: str
    duration_ms: float = 0.0


class PlannedStep(BaseModel):
    tool: str
    arguments: dict[str, Any]
    purpose: str


class Plan(BaseModel):
    goal: str
    steps: list[PlannedStep] = Field(default_factory=list)
    requested_action: str | None = None
    rejected_steps: list[str] = Field(default_factory=list)


class AgentResponse(BaseModel):
    request_id: str
    trace_id: str | None
    status: AgentStatus
    intent: Intent
    answer: str
    confidence: Confidence
    citations: list[Citation]
    requisition_id: str | None = None
    available_actions: list[str] = Field(default_factory=list)
    proposed_action: dict[str, Any] | None = None
    plan: Plan | None = None
    context: ContextEnvelope
    trajectory: list[AgentTraceStep]
    tool_calls: list[ToolInvocation]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AgentExecutionError(RuntimeError):
    """Raised when the workflow cannot run at all (as opposed to a partial answer)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class AgentState(TypedDict, total=False):
    request: str
    user: PrincipalContext
    classification: Classification
    resolution: EntityResolution
    resolved_entities: list[ResolvedEntity]
    retrieved_context: ContextEnvelope
    allowed_actions: list[ActionAvailability]
    policy_results: list[PolicyResult]
    plan: Plan
    tool_calls: list[ToolInvocation]
    observations: dict[str, Any]
    final_result: dict[str, Any]
    errors: list[str]
    trajectory: list[AgentTraceStep]


class NodeFunction(Protocol):
    def __call__(self, state: AgentState) -> dict[str, Any]: ...


class AgentWorkflow:
    def __init__(
        self,
        engine: ContextEngine,
        registry: ToolRegistry,
        composer: AnswerComposer | None = None,
    ) -> None:
        self._engine = engine
        self._registry = registry
        self._composer = composer or AnswerComposer()
        builder = StateGraph(AgentState)
        nodes: list[tuple[str, NodeFunction]] = [
            ("receive_request", self._receive_request),
            ("classify_intent", self._classify_intent),
            ("resolve_entities", self._resolve_entities),
            ("retrieve_context", self._retrieve_context),
            ("determine_allowed_actions", self._determine_allowed_actions),
            ("create_plan", self._create_plan),
            ("validate_plan", self._validate_plan),
            ("execute_tools", self._execute_tools),
            ("verify_result", self._verify_result),
            ("generate_response", self._generate_response),
        ]
        for name, function in nodes:
            builder.add_node(name, self._instrumented(name, function))
        builder.add_edge(START, "receive_request")
        builder.add_edge("receive_request", "classify_intent")
        builder.add_edge("classify_intent", "resolve_entities")
        builder.add_conditional_edges(
            "resolve_entities",
            lambda state: (
                "generate_response" if state["resolution"].clarification else "retrieve_context"
            ),
            ["retrieve_context", "generate_response"],
        )
        builder.add_conditional_edges(
            "retrieve_context",
            lambda state: (
                "determine_allowed_actions"
                if state["retrieved_context"].answerable
                else "generate_response"
            ),
            ["determine_allowed_actions", "generate_response"],
        )
        builder.add_edge("determine_allowed_actions", "create_plan")
        builder.add_edge("create_plan", "validate_plan")
        builder.add_edge("validate_plan", "execute_tools")
        builder.add_edge("execute_tools", "verify_result")
        builder.add_edge("verify_result", "generate_response")
        builder.add_edge("generate_response", END)
        self._graph = builder.compile()

    # -- infrastructure -----------------------------------------------------------------
    @staticmethod
    def _instrumented(name: str, function: NodeFunction) -> NodeFunction:
        def run(state: AgentState) -> dict[str, Any]:
            started = time.perf_counter()
            with traced(f"agent.{name}", **{"ecg.node": name}):
                update = function(state)
            step = update.pop("_step", None)
            if step is not None:
                detail, status = step
                update["trajectory"] = [
                    *state.get("trajectory", []),
                    AgentTraceStep(
                        node=name,
                        status=status,
                        detail=detail,
                        duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    ),
                ]
            return update

        return run

    @staticmethod
    def _step(detail: str, status: str = "completed") -> tuple[str, str]:
        return detail, status

    # -- nodes ------------------------------------------------------------------------------
    def _receive_request(self, state: AgentState) -> dict[str, Any]:
        question = state.get("request", "").strip()
        if not question or len(question) > MAX_QUESTION_LENGTH:
            raise AgentExecutionError("Question is empty or exceeds the input limit")
        return {
            "request": question,
            "_step": self._step(f"Accepted {len(question)}-character request"),
        }

    def _classify_intent(self, state: AgentState) -> dict[str, Any]:
        classification = self._engine.classify(state["request"])
        detail = f"{classification.intent.value} via {classification.classifier}"
        if classification.routes:
            detail += f"; routes {', '.join(route.value for route in classification.routes)}"
        return {"classification": classification, "_step": self._step(detail)}

    def _resolve_entities(self, state: AgentState) -> dict[str, Any]:
        resolution = self._engine.resolve_entities(state["classification"], state["user"])
        if resolution.clarification:
            detail, status = "Needs clarification: " + resolution.clarification, "clarification"
        else:
            detail = "Resolved " + (
                ", ".join(
                    f"{e.entity_type} {e.label}" + (f" ({e.match_band})" if e.match_band else "")
                    for e in resolution.entities
                )
                or "no entities (none required)"
            )
            status = "completed"
        return {
            "resolution": resolution,
            "resolved_entities": resolution.entities,
            "retrieved_context": self._engine.retrieve(
                state["classification"], resolution, state["user"]
            )
            if resolution.clarification
            else None,
            "_step": self._step(detail, status),
        }

    def _retrieve_context(self, state: AgentState) -> dict[str, Any]:
        envelope = self._engine.retrieve(
            state["classification"], state["resolution"], state["user"]
        )
        sources = sorted({call.tool for call in envelope.tool_calls if call.ok})
        detail = (
            f"{len(envelope.facts)} facts, {len(envelope.relationships)} relationships, "
            f"{len(envelope.documents)} documents, {len(envelope.records)} records from "
            f"{', '.join(sources) or 'no sources'}; confidence {envelope.confidence.value}"
        )
        if envelope.warnings:
            detail += f"; warnings {', '.join(envelope.warnings)}"
        status = "completed" if envelope.answerable else "degraded"
        return {
            "retrieved_context": envelope,
            "resolved_entities": envelope.entities,
            "_step": self._step(detail, status),
        }

    def _determine_allowed_actions(self, state: AgentState) -> dict[str, Any]:
        envelope = self._engine.discover_actions(state["retrieved_context"], state["user"])
        if not envelope.allowed_actions:
            detail = (
                "Policy service unavailable; failing closed"
                if "POLICY_SERVICE_UNAVAILABLE" in envelope.warnings
                else "No actions apply to this intent"
            )
        else:
            detail = f"OPA policy {envelope.freshness.policy_version}: " + ", ".join(
                f"{a.action}={a.status}" for a in envelope.allowed_actions
            )
        return {
            "retrieved_context": envelope,
            "allowed_actions": envelope.allowed_actions,
            "policy_results": envelope.policies,
            "_step": self._step(detail),
        }

    def _create_plan(self, state: AgentState) -> dict[str, Any]:
        envelope = state["retrieved_context"]
        classification = state["classification"]
        requisition = next(
            (e.entity_id for e in envelope.entities if e.entity_type == "PurchaseRequisition"),
            None,
        )
        actions = {a.action: a for a in state.get("allowed_actions", [])}
        plan = Plan(goal="Answer from retrieved context")
        if requisition and envelope.intent is Intent.REQUISITION_ELIGIBILITY:
            create = actions.get("CREATE_PURCHASE_ORDER")
            if create and create.status != "AVAILABLE":
                plan = Plan(
                    goal=f"Explain why {requisition} cannot be converted yet",
                    steps=[
                        PlannedStep(
                            tool="get_policy_explanation",
                            arguments={"requisition_id": requisition},
                            purpose="Cite the policy rules and documents behind the decision",
                        )
                    ],
                )
        elif requisition and envelope.intent is Intent.ACTION_REQUEST:
            requested = classification.requested_action or "CREATE_PURCHASE_ORDER"
            action = actions.get(requested)
            plan = Plan(
                goal=f"Handle request to {requested} for {requisition}", requested_action=requested
            )
            if action and action.executable and requested == "CREATE_PURCHASE_ORDER":
                if action.status == "AVAILABLE":
                    plan.steps = [
                        PlannedStep(
                            tool="simulate_create_purchase_order",
                            arguments={"requisition_id": requisition},
                            purpose="Dry-run the action against current state",
                        ),
                        PlannedStep(
                            tool="create_purchase_order",
                            arguments={"requisition_id": requisition},
                            purpose="Prepare a proposal for human confirmation",
                        ),
                    ]
                elif action.status == "APPROVAL_REQUIRED":
                    plan.steps = [
                        PlannedStep(
                            tool="create_purchase_order",
                            arguments={"requisition_id": requisition},
                            purpose="Prepare an approval-required proposal",
                        ),
                        PlannedStep(
                            tool="get_policy_explanation",
                            arguments={"requisition_id": requisition},
                            purpose="Explain the approval requirement",
                        ),
                    ]
                else:
                    plan.steps = [
                        PlannedStep(
                            tool="get_policy_explanation",
                            arguments={"requisition_id": requisition},
                            purpose="Explain why the action is not permitted",
                        )
                    ]
        detail = plan.goal + (
            ": " + " -> ".join(step.tool for step in plan.steps) if plan.steps else " (no tools)"
        )
        return {"plan": plan, "_step": self._step(detail)}

    def _validate_plan(self, state: AgentState) -> dict[str, Any]:
        plan = state["plan"]
        envelope = state["retrieved_context"]
        actions = {a.action: a for a in state.get("allowed_actions", [])}
        requisitions = {
            e.entity_id for e in envelope.entities if e.entity_type == "PurchaseRequisition"
        }
        valid: list[PlannedStep] = []
        rejected: list[str] = []
        for step in plan.steps[:MAX_PLAN_STEPS]:
            reason = None
            if step.tool not in PLANNER_TOOLS:
                reason = "tool not in planner allowlist"
            elif step.arguments.get("requisition_id") not in requisitions:
                reason = "argument does not match the resolved requisition"
            elif step.tool == "create_purchase_order":
                create = actions.get("CREATE_PURCHASE_ORDER")
                if create is None or create.status == "BLOCKED":
                    reason = "action is outside the valid action space"
            if reason:
                rejected.append(f"{step.tool}: {reason}")
            else:
                valid.append(step)
        rejected.extend(
            f"{step.tool}: exceeds plan step limit" for step in plan.steps[MAX_PLAN_STEPS:]
        )
        validated = plan.model_copy(update={"steps": valid, "rejected_steps": rejected})
        detail = f"{len(valid)} step(s) approved" + (
            f"; rejected {'; '.join(rejected)}" if rejected else ""
        )
        return {
            "plan": validated,
            "errors": [*state.get("errors", []), *(f"PLAN_STEP_REJECTED:{r}" for r in rejected)],
            "_step": self._step(detail, "completed" if not rejected else "corrected"),
        }

    def _execute_tools(self, state: AgentState) -> dict[str, Any]:
        context = ToolContext(principal=state["user"])
        calls: list[ToolInvocation] = []
        observations: dict[str, Any] = {}
        for step in state["plan"].steps:
            invocation = self._registry.invoke(
                step.tool, step.arguments, context, allowed=PLANNER_TOOLS
            )
            calls.append(invocation)
            if invocation.ok:
                observations[step.tool] = invocation.output
        detail = (
            ", ".join(f"{call.tool}={call.status.value}" for call in calls) or "No tools needed"
        )
        failed = [call for call in calls if not call.ok]
        return {
            "tool_calls": calls,
            "observations": observations,
            "errors": [
                *state.get("errors", []),
                *(f"TOOL_{call.status.value}:{call.tool}" for call in failed),
            ],
            "_step": self._step(detail, "degraded" if failed else "completed"),
        }

    def _verify_result(self, state: AgentState) -> dict[str, Any]:
        envelope = state["retrieved_context"]
        observations = dict(state.get("observations", {}))
        errors = list(state.get("errors", []))
        actions = {a.action: a for a in state.get("allowed_actions", [])}
        create = actions.get("CREATE_PURCHASE_ORDER")
        simulation = observations.get("simulate_create_purchase_order")
        if simulation is not None and create is not None:
            if simulation["available"] != (create.status == "AVAILABLE"):
                # State or policy changed between discovery and simulation: do not propose.
                errors.append("POLICY_DECISION_CHANGED")
                observations.pop("create_purchase_order", None)
        proposal = observations.get("create_purchase_order")
        if proposal is not None and proposal["status"] == "CONFIRMATION_REQUIRED":
            if create is None or create.status != "AVAILABLE":
                errors.append("PROPOSAL_INCONSISTENT_WITH_POLICY")
                observations.pop("create_purchase_order", None)
        detail = (
            "Consistent with policy decisions"
            if errors == state.get("errors", [])
            else ("Corrected: " + ", ".join(set(errors) - set(state.get("errors", []))))
        )
        del envelope
        return {"observations": observations, "errors": errors, "_step": self._step(detail)}

    def _generate_response(self, state: AgentState) -> dict[str, Any]:
        envelope = state.get("retrieved_context")
        if envelope is None:  # clarification during resolution
            envelope = self._engine.retrieve(
                state["classification"], state["resolution"], state["user"]
            )
        plan = state.get("plan")
        composed = self._composer.compose(
            envelope,
            state.get("observations", {}),
            requested_action=plan.requested_action if plan else None,
        )
        known_refs = self._known_refs(envelope)
        ungrounded = [
            c.ref
            for c in composed.citations
            if c.kind in {"document", "policy_rule"} and c.ref not in known_refs
        ]
        errors = list(state.get("errors", []))
        if ungrounded:
            errors.append("UNGROUNDED_CITATION:" + ",".join(ungrounded))
        if envelope.clarification:
            status: AgentStatus = "NEEDS_CLARIFICATION"
        elif envelope.confidence is Confidence.LOW:
            status = "UNAVAILABLE"
        elif envelope.confidence is Confidence.REDUCED or errors:
            status = "PARTIAL"
        else:
            status = "ANSWERED"
        return {
            "retrieved_context": envelope,
            "errors": errors,
            "final_result": {
                "status": status,
                "answer": composed.text,
                "citations": composed.citations,
                "proposal": state.get("observations", {}).get("create_purchase_order"),
            },
            "_step": self._step(f"{status} with {len(composed.citations)} citations"),
        }

    @staticmethod
    def _known_refs(envelope: ContextEnvelope) -> set[str]:
        refs = {d.document_id for d in envelope.documents}
        for action in envelope.allowed_actions:
            refs |= {rule.rule_id for rule in action.policy_rules}
        return refs

    # -- entry point -------------------------------------------------------------------------
    def invoke(self, request: AgentRequest, principal: PrincipalContext) -> AgentResponse:
        with traced("agent.run") as span:
            try:
                state: AgentState = self._graph.invoke(  # type: ignore[assignment]
                    {
                        "request": request.question,
                        "user": principal,
                        "trajectory": [],
                        "errors": [],
                    },
                    config={"recursion_limit": MAX_AGENT_STEPS},
                )
            except AgentExecutionError:
                raise
            except Exception as error:
                AGENT_RUNS.labels(intent="unknown", outcome="failed").inc()
                raise AgentExecutionError(
                    "Agent workflow failed safely", status_code=503
                ) from error
            envelope = state["retrieved_context"]
            result = state["final_result"]
            span.set_attribute("ecg.intent", envelope.intent.value)
            span.set_attribute("ecg.outcome", result["status"])
            AGENT_RUNS.labels(intent=envelope.intent.value, outcome=result["status"]).inc()
            AGENT_STEPS.observe(len(state.get("trajectory", [])))
            requisition = next(
                (e.entity_id for e in envelope.entities if e.entity_type == "PurchaseRequisition"),
                None,
            )
            return AgentResponse(
                request_id=current_request_id(),
                trace_id=current_trace_id(),
                status=result["status"],
                intent=envelope.intent,
                answer=result["answer"],
                confidence=envelope.confidence,
                citations=result["citations"],
                requisition_id=requisition,
                available_actions=[
                    a.action for a in envelope.allowed_actions if a.status == "AVAILABLE"
                ],
                proposed_action=result["proposal"],
                plan=state.get("plan"),
                context=envelope,
                trajectory=state.get("trajectory", []),
                tool_calls=[*envelope.tool_calls, *state.get("tool_calls", [])],
                warnings=envelope.warnings,
                errors=state.get("errors", []),
            )


__all__ = [
    "AgentExecutionError",
    "AgentRequest",
    "AgentResponse",
    "AgentWorkflow",
    "MAX_AGENT_STEPS",
    "PLANNER_TOOLS",
]
