from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from enterprise_context.domain.requisitions import RequisitionContext
from enterprise_context.policy.models import PolicyDecision
from enterprise_context.retrieval.models import SearchHit
from enterprise_context.retrieval.opensearch import OpenSearchError
from enterprise_context.security.principals import PrincipalContext

_REQUISITION_PATTERN = re.compile(r"\bPR\s*[-#]?\s*(\d{4,})\b", re.IGNORECASE)
MAX_QUESTION_LENGTH = 2000
MAX_AGENT_STEPS = 8


class AgentRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)


class AgentTraceStep(BaseModel):
    node: str
    status: str
    detail: str


class AgentResponse(BaseModel):
    answer: str
    requisition_id: str
    context: RequisitionContext
    policy_decision: PolicyDecision
    available_actions: list[str]
    trajectory: list[AgentTraceStep]
    supporting_documents: list[SearchHit] = Field(default_factory=list)
    context_warnings: list[str] = Field(default_factory=list)


class AnswerModel(Protocol):
    def generate(self, context: RequisitionContext, decision: PolicyDecision) -> str: ...


class DeterministicAnswerModel:
    def generate(self, context: RequisitionContext, decision: PolicyDecision) -> str:
        amount = f"{context.currency} {context.amount:,.2f}"
        supplier = context.supplier.preferred_name or "an unresolved supplier"
        if decision.allowed:
            return (
                f"{context.requisition_id} is eligible for purchase-order creation. "
                f"It is {context.state}, the supplier {supplier} is {context.supplier.status}, "
                f"and the amount is {amount}. Policy {decision.policy_version} allows the action."
            )
        reasons = " ".join(decision.explanations) or "Policy preconditions are not satisfied."
        return (
            f"{context.requisition_id} is not currently eligible for purchase-order creation. "
            f"Supplier: {supplier} ({context.supplier.status}); amount: {amount}. {reasons}"
        )


class AgentState(TypedDict, total=False):
    question: str
    principal: PrincipalContext
    requisition_id: str
    context: RequisitionContext
    supporting_documents: list[SearchHit]
    context_warnings: list[str]
    policy_decision: PolicyDecision
    available_actions: list[str]
    answer: str
    errors: list[str]
    trajectory: list[AgentTraceStep]


ContextLoader = Callable[[str, PrincipalContext], RequisitionContext | None]
DecisionEvaluator = Callable[[RequisitionContext, PrincipalContext], PolicyDecision]
DocumentSearcher = Callable[[RequisitionContext, PrincipalContext], list[SearchHit]]


class AgentExecutionError(RuntimeError):
    """Raised when the bounded workflow cannot produce a grounded response."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProcurementAgent:
    def __init__(
        self,
        context_loader: ContextLoader,
        decision_evaluator: DecisionEvaluator,
        answer_model: AnswerModel | None = None,
        document_searcher: DocumentSearcher | None = None,
    ) -> None:
        self._context_loader = context_loader
        self._decision_evaluator = decision_evaluator
        self._document_searcher = document_searcher or (lambda context, principal: [])
        self._answer_model = answer_model or DeterministicAnswerModel()
        builder = StateGraph(AgentState)
        builder.add_node("receive_request", self._receive_request)
        builder.add_node("resolve_entities", self._resolve_entities)
        builder.add_node("retrieve_context", self._retrieve_context)
        builder.add_node("determine_allowed_actions", self._determine_allowed_actions)
        builder.add_node("create_plan", self._create_plan)
        builder.add_node("verify_result", self._verify_result)
        builder.add_node("generate_response", self._generate_response)
        builder.add_edge(START, "receive_request")
        builder.add_edge("receive_request", "resolve_entities")
        builder.add_edge("resolve_entities", "retrieve_context")
        builder.add_edge("retrieve_context", "determine_allowed_actions")
        builder.add_edge("determine_allowed_actions", "create_plan")
        builder.add_edge("create_plan", "verify_result")
        builder.add_edge("verify_result", "generate_response")
        builder.add_edge("generate_response", END)
        self._graph = builder.compile()

    @staticmethod
    def _record(state: AgentState, node: str, detail: str) -> list[AgentTraceStep]:
        return [
            *state.get("trajectory", []),
            AgentTraceStep(node=node, status="completed", detail=detail),
        ]

    def _receive_request(self, state: AgentState) -> dict[str, Any]:
        question = state.get("question", "").strip()
        if not question or len(question) > MAX_QUESTION_LENGTH:
            raise AgentExecutionError("Question is empty or exceeds the input limit")
        return {
            "question": question,
            "trajectory": self._record(state, "receive_request", "Accepted bounded request"),
        }

    def _resolve_entities(self, state: AgentState) -> dict[str, Any]:
        match = _REQUISITION_PATTERN.search(state["question"])
        if match is None:
            raise AgentExecutionError("A purchase requisition identifier is required")
        requisition_id = f"PR-{int(match.group(1))}"
        return {
            "requisition_id": requisition_id,
            "trajectory": self._record(state, "resolve_entities", f"Resolved {requisition_id}"),
        }

    def _retrieve_context(self, state: AgentState) -> dict[str, Any]:
        context = self._context_loader(state["requisition_id"], state["principal"])
        if context is None:
            raise AgentExecutionError(
                "Requisition not found or outside principal scope", status_code=404
            )
        warnings: list[str] = []
        if not context.graph_available:
            warnings.append("GRAPH_CONTEXT_UNAVAILABLE")
        try:
            supporting_documents = self._document_searcher(context, state["principal"])
        except OpenSearchError:
            supporting_documents = []
            warnings.append("DOCUMENT_SEARCH_UNAVAILABLE")
        return {
            "context": context,
            "supporting_documents": supporting_documents,
            "context_warnings": warnings,
            "trajectory": self._record(
                state,
                "retrieve_context",
                f"Loaded authorized context at row version {context.row_version}; "
                f"retrieved {len(supporting_documents)} supporting documents",
            ),
        }

    def _determine_allowed_actions(self, state: AgentState) -> dict[str, Any]:
        decision = self._decision_evaluator(state["context"], state["principal"])
        actions = ["CREATE_PURCHASE_ORDER"] if decision.allowed else []
        policy_outcome = (
            "allowed" if decision.allowed else ", ".join(decision.reason_codes) or "blocked"
        )
        return {
            "policy_decision": decision,
            "available_actions": actions,
            "trajectory": self._record(
                state,
                "determine_allowed_actions",
                f"OPA policy {decision.policy_version}: {policy_outcome}",
            ),
        }

    def _create_plan(self, state: AgentState) -> dict[str, Any]:
        action = "CREATE_PURCHASE_ORDER" if state["available_actions"] else "explain_block"
        return {
            "trajectory": self._record(state, "create_plan", f"Selected bounded plan: {action}"),
        }

    def _verify_result(self, state: AgentState) -> dict[str, Any]:
        decision = state["policy_decision"]
        if decision.allowed and "CREATE_PURCHASE_ORDER" not in state["available_actions"]:
            raise AgentExecutionError("Policy result and available action set disagree")
        return {
            "trajectory": self._record(
                state, "verify_result", "Verified response against policy result"
            ),
        }

    def _generate_response(self, state: AgentState) -> dict[str, Any]:
        answer = self._answer_model.generate(state["context"], state["policy_decision"])
        warnings = state.get("context_warnings", [])
        if "GRAPH_CONTEXT_UNAVAILABLE" in warnings:
            answer += (
                " Graph context was unavailable; this decision uses transactional facts "
                "and policy only."
            )
        if "DOCUMENT_SEARCH_UNAVAILABLE" in warnings:
            answer += " Supporting document search was unavailable."
        return {
            "answer": answer,
            "trajectory": self._record(
                state,
                "generate_response",
                "Generated from structured context and policy facts",
            ),
            "supporting_documents": state.get("supporting_documents", []),
            "context_warnings": warnings,
        }

    def invoke(self, request: AgentRequest, principal: PrincipalContext) -> AgentResponse:
        try:
            state = self._graph.invoke(
                {
                    "question": request.question,
                    "principal": principal,
                    "trajectory": [],
                    "errors": [],
                },
                config={"recursion_limit": MAX_AGENT_STEPS},
            )
        except AgentExecutionError:
            raise
        except Exception as error:
            raise AgentExecutionError("Agent workflow failed safely", status_code=503) from error
        return AgentResponse(
            answer=state["answer"],
            requisition_id=state["requisition_id"],
            context=state["context"],
            policy_decision=state["policy_decision"],
            available_actions=state["available_actions"],
            trajectory=state["trajectory"],
            supporting_documents=state.get("supporting_documents", []),
            context_warnings=state.get("context_warnings", []),
        )
