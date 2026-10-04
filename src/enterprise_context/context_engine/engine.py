"""Context Engine: decides which stores answer a question and assembles the envelope.

Routing is deterministic per intent. All reads go through the typed tool registry,
so authorization, validation, timeouts and telemetry are uniform. Required sources
that fail make the envelope LOW confidence (the agent then declines to answer);
optional sources that fail add a warning and REDUCED confidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from enterprise_context.context_engine.classifier import IntentClassifier
from enterprise_context.context_engine.models import (
    Classification,
    Confidence,
    ContextEnvelope,
    DocumentRef,
    Fact,
    Freshness,
    Intent,
    Period,
    PolicyResult,
    ProvenanceRef,
    Relationship,
    ResolvedEntity,
)
from enterprise_context.domain.action_discovery import ActionAvailability
from enterprise_context.observability.tracing import traced
from enterprise_context.security.injection import contains_instruction_like_text
from enterprise_context.security.principals import PrincipalContext
from enterprise_context.tools.base import ToolContext, ToolInvocation, ToolRegistry, ToolStatus

CONTEXT_TOOLS = frozenset(
    {
        "resolve_entity",
        "search_documents",
        "query_graph",
        "query_graph_nl",
        "query_sql",
        "get_entity_context",
        "get_requisition_context",
        "get_allowed_actions",
    }
)
SUPPLIER_INTENTS = {
    Intent.SPEND_AGGREGATION,
    Intent.PURCHASE_HISTORY,
    Intent.BUYERS_FOR_SUPPLIER,
    Intent.ENTITY_LOOKUP,
}
REQUISITION_INTENTS = {Intent.REQUISITION_ELIGIBILITY, Intent.ACTION_REQUEST}
SUPPORTED_EXAMPLES = (
    "Can PR-1007 be converted to a purchase order?",
    "How much did Alice spend with Acme last year?",
    "Show all purchases involving Acme.",
    "Which suppliers for Cloud Services have active contracts?",
    "What is our policy for high-risk suppliers?",
)
PROBABLE_MARGIN = 0.05
# Warnings that describe the answer without making its facts less reliable.
INFORMATIONAL_WARNINGS = frozenset(
    {"UNTRUSTED_INSTRUCTIONS_IN_DOCUMENT", "RESULTS_TRUNCATED", "MULTIPLE_REQUISITIONS_FIRST_USED"}
)


@dataclass
class EntityResolution:
    entities: list[ResolvedEntity] = field(default_factory=list)
    calls: list[ToolInvocation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarification: str | None = None

    def first(self, entity_type: str) -> ResolvedEntity | None:
        return next((e for e in self.entities if e.entity_type == entity_type), None)


@dataclass
class _Builder:
    classification: Classification
    calls: list[ToolInvocation]
    warnings: list[str]
    entities: list[ResolvedEntity]
    facts: list[Fact] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    documents: list[DocumentRef] = field(default_factory=list)
    provenance: list[ProvenanceRef] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    clarification: str | None = None
    required_failed: bool = False


def _short(uri: str) -> str:
    return uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


class ContextEngine:
    def __init__(
        self,
        registry: ToolRegistry,
        classifier: IntentClassifier,
        *,
        graph_version: Callable[[], str | None] = lambda: None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._registry = registry
        self._classifier = classifier
        self._graph_version = graph_version
        self._today = today

    # -- tool access ---------------------------------------------------------------
    def _call(
        self, calls: list[ToolInvocation], tool: str, arguments: dict[str, Any], ctx: ToolContext
    ) -> ToolInvocation:
        invocation = self._registry.invoke(tool, arguments, ctx, allowed=CONTEXT_TOOLS)
        calls.append(invocation)
        return invocation

    # -- stage 1 -----------------------------------------------------------------------
    def classify(self, question: str) -> Classification:
        with traced("context.classify") as span:
            classification = self._classifier.classify(question)
            span.set_attribute("ecg.intent", classification.intent.value)
            return classification

    # -- stage 2 -----------------------------------------------------------------------
    def resolve_entities(
        self, classification: Classification, principal: PrincipalContext
    ) -> EntityResolution:
        ctx = ToolContext(principal=principal)
        resolution = EntityResolution()
        with traced("context.resolve_entities", **{"ecg.intent": classification.intent.value}):
            for requisition_id in classification.requisition_ids[:1]:
                resolution.entities.append(
                    ResolvedEntity(
                        entity_type="PurchaseRequisition",
                        entity_id=requisition_id,
                        label=requisition_id,
                        mention=requisition_id,
                        confidence=1.0,
                        method="EXACT_IDENTIFIER",
                    )
                )
            if len(classification.requisition_ids) > 1:
                resolution.warnings.append("MULTIPLE_REQUISITIONS_FIRST_USED")
            if classification.category:
                resolution.entities.append(
                    ResolvedEntity(
                        entity_type="ProductCategory",
                        entity_id=classification.category,
                        label=classification.category,
                        mention=classification.category,
                        confidence=1.0,
                        method="TAXONOMY_LABEL",
                    )
                )
            needs_supplier = classification.intent in SUPPLIER_INTENTS or (
                classification.intent is Intent.GRAPH_TRAVERSAL
                and classification.graph_template != "suppliers_for_category_with_active_contracts"
            )
            if needs_supplier or classification.intent is Intent.GRAPH_QUESTION:
                self._resolve_supplier(classification, resolution, ctx, required=needs_supplier)
            if classification.buyer_mention and resolution.clarification is None:
                self._resolve_buyer(classification.buyer_mention, resolution, ctx)
        return resolution

    def _resolve_supplier(
        self,
        classification: Classification,
        resolution: EntityResolution,
        ctx: ToolContext,
        *,
        required: bool,
    ) -> None:
        for mention in classification.supplier_mentions[:2]:
            attempts = [mention]
            words = mention.split()
            if len(words) > 1:
                attempts.append(" ".join(words[:-1]))
            for attempt in attempts:
                result = self._call(
                    resolution.calls, "resolve_entity", {"query": attempt, "limit": 3}, ctx
                )
                if not result.ok:
                    resolution.warnings.append("ENTITY_RESOLUTION_UNAVAILABLE")
                    if required:
                        resolution.clarification = (
                            "Entity resolution is unavailable, so the supplier could not be "
                            "identified."
                        )
                    return
                candidates = result.output["candidates"]
                if not candidates:
                    continue
                top = candidates[0]
                runner_up = candidates[1] if len(candidates) > 1 else None
                close = runner_up is not None and (
                    runner_up["confidence_score"] >= top["confidence_score"] - PROBABLE_MARGIN
                )
                if top["match_band"] == "MATCH" and not close:
                    self._accept(resolution, mention, top)
                    return
                if top["match_band"] == "PROBABLE" and not close:
                    self._accept(resolution, mention, top)
                    resolution.warnings.append(f"ENTITY_MATCH_PROBABLE:{mention}")
                    return
                names = ", ".join(sorted({c["preferred_name"] for c in candidates[:3]}))
                resolution.clarification = (
                    f"'{mention}' could refer to more than one supplier ({names}). "
                    "Which one do you mean?"
                    if close
                    else f"Did you mean {top['preferred_name']} for '{mention}'?"
                )
                return
        if required:
            mentioned = ", ".join(f"'{m}'" for m in classification.supplier_mentions)
            resolution.clarification = (
                f"No supplier you are authorized to see matched {mentioned}."
                if mentioned
                else "Which supplier do you mean?"
            )

    @staticmethod
    def _accept(resolution: EntityResolution, mention: str, candidate: dict[str, Any]) -> None:
        resolution.entities.append(
            ResolvedEntity(
                entity_type="Supplier",
                entity_id=candidate["canonical_entity_id"],
                label=candidate["preferred_name"],
                mention=mention,
                confidence=float(candidate["confidence_score"] or 0),
                match_band=candidate.get("match_band"),
                method=candidate.get("match_method"),
                review_required=bool(candidate.get("review_required")),
            )
        )

    def _resolve_buyer(self, name: str, resolution: EntityResolution, ctx: ToolContext) -> None:
        result = self._call(
            resolution.calls,
            "query_sql",
            {"query_name": "find_buyers", "parameters": {"name": name, "limit": 5}},
            ctx,
        )
        if not result.ok:
            resolution.warnings.append("BUYER_LOOKUP_UNAVAILABLE")
            return
        rows = result.output["rows"]
        if len(rows) == 1:
            resolution.entities.append(
                ResolvedEntity(
                    entity_type="Buyer",
                    entity_id=rows[0]["buyer_id"],
                    label=rows[0]["buyer_name"],
                    mention=name,
                    confidence=1.0,
                    method="NAME_LOOKUP",
                )
            )
        elif not rows:
            resolution.clarification = f"No buyer named '{name}' is visible in your scope."
        else:
            names = ", ".join(row["buyer_name"] for row in rows[:5])
            resolution.clarification = f"'{name}' matches several buyers ({names}). Which one?"

    # -- stage 3 -----------------------------------------------------------------------
    def retrieve(
        self,
        classification: Classification,
        resolution: EntityResolution,
        principal: PrincipalContext,
    ) -> ContextEnvelope:
        ctx = ToolContext(principal=principal)
        builder = _Builder(
            classification=classification,
            calls=list(resolution.calls),
            warnings=list(resolution.warnings),
            entities=list(resolution.entities),
            clarification=resolution.clarification,
        )
        with traced("context.retrieve", **{"ecg.intent": classification.intent.value}):
            if builder.clarification is None:
                handler = {
                    Intent.REQUISITION_ELIGIBILITY: self._requisition,
                    Intent.ACTION_REQUEST: self._requisition,
                    Intent.SPEND_AGGREGATION: self._spend,
                    Intent.PURCHASE_HISTORY: self._history,
                    Intent.BUYERS_FOR_SUPPLIER: self._buyers,
                    Intent.ENTITY_LOOKUP: self._entity_lookup,
                    Intent.GRAPH_TRAVERSAL: self._graph_traversal,
                    Intent.GRAPH_QUESTION: self._graph_question,
                    Intent.POLICY_LOOKUP: self._policy_lookup,
                    Intent.UNSUPPORTED: self._unsupported,
                }[classification.intent]
                handler(builder, resolution, ctx)
        return self._envelope(builder)

    def _envelope(self, builder: _Builder) -> ContextEnvelope:
        try:
            graph_projection = self._graph_version()
        except Exception:  # freshness metadata must never break context assembly
            graph_projection = None
        if builder.required_failed:
            confidence = Confidence.LOW
        elif set(builder.warnings) - INFORMATIONAL_WARNINGS or any(
            e.match_band == "PROBABLE" for e in builder.entities
        ):
            confidence = Confidence.REDUCED
        else:
            confidence = Confidence.HIGH
        return ContextEnvelope(
            question=builder.classification.question,
            intent=builder.classification.intent,
            routes=builder.classification.routes,
            entities=builder.entities,
            relationships=builder.relationships,
            facts=builder.facts,
            documents=builder.documents,
            provenance=builder.provenance,
            records=builder.records,
            freshness=Freshness(retrieved_at=datetime.now(UTC), graph_projection=graph_projection),
            confidence=confidence,
            warnings=sorted(set(builder.warnings)),
            clarification=builder.clarification,
            tool_calls=builder.calls,
            classification=builder.classification,
        )

    def _require(self, builder: _Builder, result: ToolInvocation, what: str) -> bool:
        if result.ok:
            return True
        if result.status is ToolStatus.NOT_FOUND:
            builder.clarification = f"{what} was not found or is outside your authorized scope."
        elif result.status in {ToolStatus.DENIED, ToolStatus.NOT_ALLOWED}:
            builder.clarification = f"You are not authorized to read {what}."
        else:
            builder.warnings.append(f"{result.tool.upper()}_{result.status.value}")
        builder.required_failed = True
        return False

    def _search(
        self, builder: _Builder, query: str, document_types: list[str], ctx: ToolContext
    ) -> ToolInvocation:
        result = self._call(
            builder.calls,
            "search_documents",
            {"query": query[:500], "document_types": document_types, "limit": 5},
            ctx,
        )
        if result.ok:
            for hit in result.output["hits"]:
                flagged = contains_instruction_like_text(f"{hit['title']} {hit['content']}")
                if flagged:
                    builder.warnings.append("UNTRUSTED_INSTRUCTIONS_IN_DOCUMENT")
                builder.documents.append(
                    DocumentRef(
                        document_id=hit["document_id"],
                        title=hit["title"],
                        snippet="" if flagged else hit["content"][:400],
                        document_type=hit["document_type"],
                        source_record_id=hit["source_record_id"],
                        rrf_rank=hit["rrf_rank"],
                        bm25_rank=hit.get("bm25_rank"),
                        vector_rank=hit.get("vector_rank"),
                        flagged_instructions=flagged,
                    )
                )
        else:
            builder.warnings.append("DOCUMENT_SEARCH_UNAVAILABLE")
        return result

    def _provenance_from_graph(self, builder: _Builder, entity_id: str, ctx: ToolContext) -> None:
        result = self._call(
            builder.calls,
            "query_graph",
            {"template": "entity_provenance", "parameters": {"entity_id": entity_id}},
            ctx,
        )
        if not result.ok:
            builder.warnings.append("GRAPH_CONTEXT_UNAVAILABLE")
            return
        for row in result.output["rows"]:
            builder.provenance.append(
                ProvenanceRef(
                    entity_id=entity_id,
                    source_system=row.get("sourceSystem", "unknown"),
                    source_record_id=row.get("sourceRecordId", "unknown"),
                    detail=(
                        f"Recorded as '{row.get('alias', '')}' "
                        f"({row.get('method', 'n/a')}, confidence {row.get('confidence', 'n/a')})"
                    ),
                )
            )

    # -- intent handlers ---------------------------------------------------------------
    def _requisition(
        self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext
    ) -> None:
        requisition = resolution.first("PurchaseRequisition")
        if requisition is None:
            builder.clarification = "Which purchase requisition do you mean?"
            return
        result = self._call(
            builder.calls,
            "get_requisition_context",
            {"requisition_id": requisition.entity_id},
            ctx,
        )
        if not self._require(builder, result, requisition.entity_id):
            return
        context = result.output
        rid = context["requisition_id"]
        supplier = context["supplier"]

        def fact(predicate: str, value: Any, source_system: str = "ERP") -> None:
            builder.facts.append(
                Fact(
                    subject=rid,
                    predicate=predicate,
                    value=str(value),
                    source="postgres",
                    source_system=source_system,
                )
            )

        fact("state", context["state"])
        fact("amount", f"{context['currency']} {context['amount']}")
        fact("buyer", f"{context['buyer_name']} ({context['buyer_id']})")
        fact("business_unit", context["business_unit_id"])
        fact("categories", ", ".join(context["categories"]) or "none")
        fact("supplier", supplier["preferred_name"] or "UNRESOLVED")
        fact("supplier_status", supplier["status"], "SUPPLIER_MASTER")
        fact("supplier_risk", supplier["risk_rating"], "SUPPLIER_MASTER")
        fact("active_contracts", ", ".join(context["active_contract_ids"]) or "none", "CONTRACTS")
        fact("row_version", context["row_version"])
        if supplier["canonical_entity_id"]:
            builder.entities.append(
                ResolvedEntity(
                    entity_type="Supplier",
                    entity_id=supplier["canonical_entity_id"],
                    label=supplier["preferred_name"] or supplier["canonical_entity_id"],
                    mention=rid,
                    confidence=1.0,
                    method="REQUISITION_LINK",
                )
            )
        else:
            builder.warnings.append("SUPPLIER_UNRESOLVED")
        builder.entities.append(
            ResolvedEntity(
                entity_type="Buyer",
                entity_id=context["buyer_id"],
                label=context["buyer_name"],
                mention=rid,
                confidence=1.0,
                method="REQUISITION_LINK",
            )
        )
        if context.get("graph_available"):
            for graph_fact in context.get("graph_facts", []):
                builder.relationships.append(
                    Relationship(
                        subject=rid,
                        predicate=graph_fact["predicate"],
                        object=_short(graph_fact["object_value"]),
                    )
                )
        else:
            builder.warnings.append("GRAPH_CONTEXT_UNAVAILABLE")
        if supplier["canonical_entity_id"]:
            self._provenance_from_graph(builder, supplier["canonical_entity_id"], ctx)
        self._search(
            builder,
            f"purchase order conversion requisition {context['state']} supplier "
            f"{supplier['status']} risk {supplier['risk_rating']} approval limit "
            + " ".join(context["categories"]),
            ["PROCUREMENT_POLICY", "CONTRACT"],
            ctx,
        )

    def _supplier(self, builder: _Builder, resolution: EntityResolution) -> ResolvedEntity | None:
        supplier = resolution.first("Supplier")
        if supplier is None:
            builder.clarification = builder.clarification or "Which supplier do you mean?"
        return supplier

    def _period(self, classification: Classification) -> Period:
        if classification.period is not None:
            return classification.period
        today = self._today()
        return Period(
            start=today - timedelta(days=365),
            end=today + timedelta(days=1),
            label="last 12 months",
        )

    def _spend(self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext) -> None:
        supplier = self._supplier(builder, resolution)
        if supplier is None:
            return
        period = self._period(builder.classification)
        buyer = resolution.first("Buyer")
        parameters: dict[str, Any] = {
            "canonical_entity_id": supplier.entity_id,
            "period_start": period.start.isoformat(),
            "period_end": period.end.isoformat(),
        }
        if buyer:
            parameters["buyer_id"] = buyer.entity_id
        result = self._call(
            builder.calls,
            "query_sql",
            {"query_name": "spend_by_supplier", "parameters": parameters},
            ctx,
        )
        if not self._require(builder, result, "spend data"):
            return
        builder.records = result.output["rows"]
        subject = f"{buyer.label} with {supplier.label}" if buyer else supplier.label
        for row in builder.records:
            builder.facts.append(
                Fact(
                    subject=subject,
                    predicate=f"spend_{period.label}_{row['currency']}",
                    value=(
                        f"{row['currency']} {row['total_amount']} over "
                        f"{row['priced_orders']} priced of {row['purchase_orders']} orders"
                    ),
                    source="postgres",
                    source_system="ERP",
                )
            )
            if row["missing_amount_orders"]:
                builder.warnings.append("SPEND_EXCLUDES_ORDERS_WITH_MISSING_AMOUNTS")

    def _history(self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext) -> None:
        supplier = self._supplier(builder, resolution)
        if supplier is None:
            return
        result = self._call(
            builder.calls,
            "query_sql",
            {
                "query_name": "purchases_for_supplier",
                "parameters": {"canonical_entity_id": supplier.entity_id, "limit": 25},
            },
            ctx,
        )
        if not self._require(builder, result, "purchase history"):
            return
        builder.records = result.output["rows"]
        if result.output.get("next_offset") is not None:
            builder.warnings.append("RESULTS_TRUNCATED")
        self._aliases(builder, supplier, ctx)

    def _aliases(self, builder: _Builder, supplier: ResolvedEntity, ctx: ToolContext) -> None:
        result = self._call(
            builder.calls, "get_entity_context", {"entity_id": supplier.entity_id}, ctx
        )
        if not result.ok:
            builder.warnings.append("ENTITY_CONTEXT_UNAVAILABLE")
            return
        for alias in result.output["aliases"]:
            builder.facts.append(
                Fact(
                    subject=supplier.label,
                    predicate="alias_review_candidate" if alias["review_required"] else "alias",
                    value=(
                        f"{alias['alias']} ({alias['source_system']}, "
                        f"{alias['resolution_method']}, {alias['confidence_score']})"
                    ),
                    source="postgres",
                    source_system=alias["source_system"],
                    source_record_id=alias["source_record_id"],
                )
            )

    def _buyers(self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext) -> None:
        supplier = self._supplier(builder, resolution)
        if supplier is None:
            return
        result = self._call(
            builder.calls,
            "query_sql",
            {
                "query_name": "buyers_for_supplier",
                "parameters": {"canonical_entity_id": supplier.entity_id, "limit": 25},
            },
            ctx,
        )
        if self._require(builder, result, "buyer data"):
            builder.records = result.output["rows"]

    def _entity_lookup(
        self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext
    ) -> None:
        supplier = self._supplier(builder, resolution)
        if supplier is None:
            return
        self._aliases(builder, supplier, ctx)
        if "ENTITY_CONTEXT_UNAVAILABLE" in builder.warnings:
            builder.required_failed = True
            return
        self._provenance_from_graph(builder, supplier.entity_id, ctx)

    def _graph_traversal(
        self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext
    ) -> None:
        template = builder.classification.graph_template or ""
        if template == "suppliers_for_category_with_active_contracts":
            parameters: dict[str, Any] = {"category": builder.classification.category}
        else:
            supplier = self._supplier(builder, resolution)
            if supplier is None:
                return
            parameters = {"entity_id": supplier.entity_id}
        result = self._call(
            builder.calls, "query_graph", {"template": template, "parameters": parameters}, ctx
        )
        if not self._require(builder, result, "graph context"):
            builder.warnings.append("GRAPH_CONTEXT_UNAVAILABLE")
            return
        builder.records = result.output["rows"]
        if result.output.get("boolean") is not None:
            builder.records = [{"answer": result.output["boolean"]}]

    def _graph_question(
        self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext
    ) -> None:
        uris = [
            f"https://example.org/enterprise-context/supplier/{entity.entity_id}"
            for entity in resolution.entities
            if entity.entity_type == "Supplier"
        ]
        result = self._call(
            builder.calls,
            "query_graph_nl",
            {"question": builder.classification.question, "entity_uris": uris},
            ctx,
        )
        if result.status is ToolStatus.INVALID:
            builder.clarification = (
                "I could not build a safe, valid graph query for that question. "
                "Try rephrasing it around suppliers, categories, contracts or requisitions."
            )
            return
        if not self._require(builder, result, "graph context"):
            return
        builder.records = result.output["rows"]
        builder.warnings.extend(result.output.get("warnings", []))
        builder.warnings.append("GENERATED_SPARQL_USED")

    def _policy_lookup(
        self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext
    ) -> None:
        del resolution
        result = self._search(builder, builder.classification.question, ["PROCUREMENT_POLICY"], ctx)
        if not result.ok:
            builder.required_failed = True

    def _unsupported(
        self, builder: _Builder, resolution: EntityResolution, ctx: ToolContext
    ) -> None:
        del resolution, ctx
        builder.clarification = (
            "I can answer questions about requisition eligibility, supplier spend and "
            "purchase history, entity aliases, graph relationships and procurement policy. "
            "For example: " + " | ".join(SUPPORTED_EXAMPLES)
        )

    # -- action discovery -------------------------------------------------------------
    def discover_actions(
        self, envelope: ContextEnvelope, principal: PrincipalContext
    ) -> ContextEnvelope:
        requisition = next(
            (e for e in envelope.entities if e.entity_type == "PurchaseRequisition"), None
        )
        if requisition is None or envelope.intent not in REQUISITION_INTENTS:
            return envelope
        if envelope.clarification is not None or envelope.confidence is Confidence.LOW:
            return envelope
        calls = list(envelope.tool_calls)
        result = self._call(
            calls,
            "get_allowed_actions",
            {"requisition_id": requisition.entity_id},
            ToolContext(principal=principal),
        )
        update: dict[str, Any] = {"tool_calls": calls}
        if not result.ok:
            # Fail closed: without a policy decision no action is presented as available.
            update["warnings"] = sorted({*envelope.warnings, "POLICY_SERVICE_UNAVAILABLE"})
            update["confidence"] = Confidence.LOW
            return envelope.model_copy(update=update)
        actions = [ActionAvailability.model_validate(a) for a in result.output["actions"]]
        update["allowed_actions"] = actions
        update["policies"] = [
            PolicyResult(
                action=action.action,
                status=action.status,
                reason_codes=action.reason_codes,
                explanations=action.explanations,
                rules=action.policy_rules,
                policy_version=result.output["policy_version"],
            )
            for action in actions
        ]
        update["freshness"] = envelope.freshness.model_copy(
            update={"policy_version": result.output["policy_version"]}
        )
        return envelope.model_copy(update=update)

    def build(self, question: str, principal: PrincipalContext) -> ContextEnvelope:
        classification = self.classify(question)
        resolution = self.resolve_entities(classification, principal)
        envelope = self.retrieve(classification, resolution, principal)
        return self.discover_actions(envelope, principal)
