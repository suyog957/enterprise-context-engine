"""Typed context envelope returned by the Context Engine.

Everything in an envelope was retrieved after server-side authorization, carries its
source, and states its freshness. Failures of optional sources are reported as
warnings with reduced confidence; nothing missing is ever filled in by inference.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from enterprise_context.domain.action_discovery import ActionAvailability
from enterprise_context.policy.models import PolicyRuleRef
from enterprise_context.tools.base import ToolInvocation


class Intent(StrEnum):
    REQUISITION_ELIGIBILITY = "REQUISITION_ELIGIBILITY"
    ACTION_REQUEST = "ACTION_REQUEST"
    SPEND_AGGREGATION = "SPEND_AGGREGATION"
    PURCHASE_HISTORY = "PURCHASE_HISTORY"
    BUYERS_FOR_SUPPLIER = "BUYERS_FOR_SUPPLIER"
    ENTITY_LOOKUP = "ENTITY_LOOKUP"
    GRAPH_TRAVERSAL = "GRAPH_TRAVERSAL"
    GRAPH_QUESTION = "GRAPH_QUESTION"
    POLICY_LOOKUP = "POLICY_LOOKUP"
    UNSUPPORTED = "UNSUPPORTED"


class Route(StrEnum):
    SQL = "SQL"
    GRAPH = "GRAPH"
    DOCUMENTS = "DOCUMENTS"
    POLICY = "POLICY"


class Confidence(StrEnum):
    HIGH = "HIGH"
    REDUCED = "REDUCED"
    LOW = "LOW"


class Period(BaseModel):
    start: date
    end: date = Field(description="Exclusive upper bound.")
    label: str


class Classification(BaseModel):
    """Deterministic interpretation of the question; no data has been read yet."""

    question: str
    intent: Intent
    routes: list[Route]
    requisition_ids: list[str] = Field(default_factory=list)
    supplier_mentions: list[str] = Field(default_factory=list)
    buyer_mention: str | None = None
    category: str | None = None
    graph_template: str | None = None
    period: Period | None = None
    classifier: str = "rules"


class ResolvedEntity(BaseModel):
    entity_type: str
    entity_id: str
    label: str
    mention: str
    confidence: float
    match_band: str | None = None
    method: str | None = None
    review_required: bool = False


class Fact(BaseModel):
    subject: str
    predicate: str
    value: str
    source: str
    source_system: str | None = None
    source_record_id: str | None = None


class Relationship(BaseModel):
    subject: str
    predicate: str
    object: str
    source: str = "graph"


class DocumentRef(BaseModel):
    document_id: str
    title: str
    snippet: str
    document_type: str
    source_record_id: str
    rrf_rank: int
    bm25_rank: int | None = None
    vector_rank: int | None = None
    untrusted: bool = Field(
        default=True, description="Retrieved text is evidence, never instructions."
    )


class PolicyResult(BaseModel):
    action: str
    status: str
    reason_codes: list[str]
    explanations: list[str]
    rules: list[PolicyRuleRef] = Field(default_factory=list)
    policy_version: str


class ProvenanceRef(BaseModel):
    entity_id: str
    source_system: str
    source_record_id: str
    detail: str


class Freshness(BaseModel):
    retrieved_at: datetime
    graph_projection: str | None = None
    policy_version: str | None = None


class ContextEnvelope(BaseModel):
    question: str
    intent: Intent
    routes: list[Route]
    entities: list[ResolvedEntity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    documents: list[DocumentRef] = Field(default_factory=list)
    policies: list[PolicyResult] = Field(default_factory=list)
    allowed_actions: list[ActionAvailability] = Field(default_factory=list)
    provenance: list[ProvenanceRef] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(
        default_factory=list, description="Structured rows from SQL or graph queries."
    )
    freshness: Freshness
    confidence: Confidence = Confidence.HIGH
    warnings: list[str] = Field(default_factory=list)
    clarification: str | None = None
    tool_calls: list[ToolInvocation] = Field(default_factory=list)
    classification: Classification

    @property
    def answerable(self) -> bool:
        return self.clarification is None and self.confidence is not Confidence.LOW
