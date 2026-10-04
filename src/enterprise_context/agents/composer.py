"""Grounded answer composition.

The default composer is deterministic: every sentence is built from the authorized
context envelope and tool observations, and every claim carries a citation. It never
copies text from documents flagged as containing instructions. A language model can
later rephrase this output, but verification checks citations against the envelope.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel

from enterprise_context.context_engine.models import Confidence, ContextEnvelope, Intent
from enterprise_context.domain.action_discovery import ActionAvailability

CitationKind = Literal["fact", "document", "policy_rule", "record", "entity", "graph"]

WARNING_TEXT = {
    "GRAPH_CONTEXT_UNAVAILABLE": (
        "Graph context was unavailable, so relationship and provenance details are omitted; "
        "the decision uses transactional facts and policy only."
    ),
    "DOCUMENT_SEARCH_UNAVAILABLE": "Supporting document search was unavailable.",
    "POLICY_SERVICE_UNAVAILABLE": (
        "The policy service is unavailable, so no action can be confirmed as permitted."
    ),
    "SPEND_EXCLUDES_ORDERS_WITH_MISSING_AMOUNTS": (
        "Some orders have no recorded amount; they are counted but not summed."
    ),
    "UNTRUSTED_INSTRUCTIONS_IN_DOCUMENT": (
        "A retrieved document contained instruction-like text; it was treated as untrusted "
        "data and its text was not used."
    ),
    "GENERATED_SPARQL_USED": (
        "No predefined query fitted, so a generated and validated read-only SPARQL query was used."
    ),
    "GENERATED_QUERY_ROWS_WITHHELD_BY_SCOPE": (
        "Some rows referenced records outside your business units and were withheld."
    ),
    "RESULTS_TRUNCATED": "Only the first page of results is shown.",
    "SUPPLIER_UNRESOLVED": (
        "The requisition's supplier could not be resolved to a canonical entity."
    ),
}


class Citation(BaseModel):
    kind: CitationKind
    ref: str
    label: str


class ComposedAnswer(BaseModel):
    text: str
    citations: list[Citation]


def _money(value: Any, currency: str) -> str:
    try:
        return f"{currency} {Decimal(str(value)):,.2f}"
    except (InvalidOperation, ValueError):
        return f"{currency} {value}"


def _fact(envelope: ContextEnvelope, predicate: str) -> str | None:
    return next((f.value for f in envelope.facts if f.predicate == predicate), None)


def _action(envelope: ContextEnvelope, name: str) -> ActionAvailability | None:
    return next((a for a in envelope.allowed_actions if a.action == name), None)


class AnswerComposer:
    def compose(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        *,
        requested_action: str | None = None,
    ) -> ComposedAnswer:
        citations: list[Citation] = []
        if envelope.clarification:
            return ComposedAnswer(text=envelope.clarification, citations=[])
        if envelope.confidence is Confidence.LOW:
            reasons = [WARNING_TEXT.get(w, w) for w in envelope.warnings] or [
                "a required data source did not respond"
            ]
            return ComposedAnswer(
                text="I can't answer this reliably right now: "
                + " ".join(reasons)
                + " Nothing was inferred to fill the gap.",
                citations=[],
            )
        handler = {
            Intent.REQUISITION_ELIGIBILITY: self._eligibility,
            Intent.ACTION_REQUEST: self._action_request,
            Intent.SPEND_AGGREGATION: self._spend,
            Intent.PURCHASE_HISTORY: self._history,
            Intent.BUYERS_FOR_SUPPLIER: self._buyers,
            Intent.ENTITY_LOOKUP: self._entity_lookup,
            Intent.GRAPH_TRAVERSAL: self._graph,
            Intent.GRAPH_QUESTION: self._graph_question,
            Intent.POLICY_LOOKUP: self._policy,
        }.get(envelope.intent)
        if handler is None:
            return ComposedAnswer(text="I can't help with that question.", citations=[])
        sentences = handler(envelope, observations, citations, requested_action)
        for entity in envelope.entities:
            if entity.match_band == "PROBABLE":
                sentences.append(
                    f"Note: '{entity.mention}' was matched to {entity.label} with "
                    f"{entity.confidence:.0%} confidence (probable match)."
                )
                citations.append(Citation(kind="entity", ref=entity.entity_id, label=entity.label))
        sentences.extend(WARNING_TEXT[w] for w in envelope.warnings if w in WARNING_TEXT)
        return ComposedAnswer(text=" ".join(s for s in sentences if s), citations=citations)

    # -- requisitions -----------------------------------------------------------------
    @staticmethod
    def _requisition_summary(envelope: ContextEnvelope, citations: list[Citation]) -> str:
        rid = envelope.facts[0].subject if envelope.facts else "The requisition"
        for predicate in ("state", "supplier_status", "amount"):
            if _fact(envelope, predicate):
                citations.append(
                    Citation(
                        kind="fact", ref=f"{rid}.{predicate}", label=predicate.replace("_", " ")
                    )
                )
        return (
            f"{rid} is {_fact(envelope, 'state')}; supplier {_fact(envelope, 'supplier')} is "
            f"{_fact(envelope, 'supplier_status')} (risk {_fact(envelope, 'supplier_risk')}); "
            f"amount {_fact(envelope, 'amount')}; buyer {_fact(envelope, 'buyer')}."
        )

    @staticmethod
    def _rules(action: ActionAvailability, citations: list[Citation]) -> str:
        for rule in action.policy_rules:
            citations.append(
                Citation(
                    kind="policy_rule",
                    ref=rule.rule_id,
                    label=f"{rule.reason_code} ({rule.document_id})",
                )
            )
        rule_ids = ", ".join(rule.rule_id for rule in action.policy_rules)
        reasons = " ".join(action.explanations)
        return f"{reasons} (policy rules: {rule_ids})." if rule_ids else reasons

    def _other_actions(self, envelope: ContextEnvelope) -> str:
        available = [
            a.action
            for a in envelope.allowed_actions
            if a.status == "AVAILABLE" and a.action != "CREATE_PURCHASE_ORDER"
        ]
        unavailable = [
            f"{a.action} ({', '.join(a.reason_codes)})"
            for a in envelope.allowed_actions
            if a.status != "AVAILABLE" and a.action != "CREATE_PURCHASE_ORDER"
        ]
        parts = []
        if available:
            parts.append("Other actions available to you: " + ", ".join(available) + ".")
        if unavailable:
            parts.append("Unavailable: " + "; ".join(unavailable) + ".")
        return " ".join(parts)

    def _eligibility(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        del requested_action
        summary = self._requisition_summary(envelope, citations)
        action = _action(envelope, "CREATE_PURCHASE_ORDER")
        if action is None:
            return [summary, "No policy decision is available for purchase-order creation."]
        version = envelope.freshness.policy_version
        if action.status == "AVAILABLE":
            head = (
                f"Yes - it can be converted to a purchase order. Policy {version} allows "
                "CREATE_PURCHASE_ORDER for you."
            )
        elif action.status == "APPROVAL_REQUIRED":
            head = (
                "Not yet - purchase-order creation requires manager approval first. "
                + self._rules(action, citations)
            )
        else:
            head = "No - it cannot become a purchase order. " + self._rules(action, citations)
        sentences = [head, summary, self._other_actions(envelope)]
        sentences.append(self._documents(envelope, observations, citations))
        return sentences

    def _action_request(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        requested = requested_action or "CREATE_PURCHASE_ORDER"
        summary = self._requisition_summary(envelope, citations)
        action = _action(envelope, requested)
        if action is None:
            return [f"{requested} is not a recognised action for this requisition.", summary]
        if not action.executable:
            state = "permitted" if action.status == "AVAILABLE" else "not permitted"
            return [
                f"{requested} is {state} for you"
                + ("" if action.status == "AVAILABLE" else f" ({', '.join(action.reason_codes)})")
                + ", but this release does not execute it from chat.",
                summary,
            ]
        proposal = observations.get("create_purchase_order")
        if action.status == "BLOCKED" or proposal is None:
            return [
                "I did not attempt to create a purchase order because policy does not permit it. "
                + self._rules(action, citations),
                summary,
            ]
        if proposal["status"] == "APPROVAL_REQUIRED":
            return [
                "A purchase order needs manager approval first. "
                + self._rules(action, citations)
                + " Submit an approval request; once a manager approves it, confirm the "
                "purchase order with an idempotency key.",
                summary,
            ]
        mode = (
            "Dry-run mode is on, so confirming will simulate the write without creating a record."
            if proposal["dry_run"]
            else "Confirming will create the purchase order."
        )
        simulation = observations.get("simulate_create_purchase_order")
        simulated = " The simulation also returned 'allowed'." if simulation else ""
        return [
            f"{summary} Policy allows CREATE_PURCHASE_ORDER.{simulated} I have prepared a "
            "purchase-order proposal; it needs your explicit confirmation, which executes "
            "through the protected endpoint with a fresh policy check and an idempotency key.",
            mode,
        ]

    @staticmethod
    def _documents(
        envelope: ContextEnvelope, observations: dict[str, Any], citations: list[Citation]
    ) -> str:
        explanation = observations.get("get_policy_explanation") or {}
        cited = {doc["document_id"] for doc in explanation.get("supporting_documents", [])}
        documents = [d for d in envelope.documents if not d.flagged_instructions]
        chosen = [d for d in documents if d.document_id in cited] or documents[:2]
        for document in chosen:
            citations.append(
                Citation(kind="document", ref=document.document_id, label=document.title)
            )
        if not chosen:
            return ""
        return (
            "Relevant documents: " + "; ".join(f"{d.title} ({d.document_id})" for d in chosen) + "."
        )

    # -- SQL routes ----------------------------------------------------------------------
    def _spend(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        del observations, requested_action
        supplier = next(e for e in envelope.entities if e.entity_type == "Supplier")
        buyer = next((e for e in envelope.entities if e.entity_type == "Buyer"), None)
        period = envelope.classification.period
        label = period.label if period else "the last 12 months"
        who = f"{buyer.label} spent" if buyer else "Spend was"
        citations.append(
            Citation(kind="record", ref="sql:spend_by_supplier", label="ERP purchase orders")
        )
        if not envelope.records:
            return [
                f"No non-cancelled purchase orders with {supplier.label} in {label} "
                "were found in your scope."
            ]
        totals = "; ".join(
            f"{_money(row['total_amount'], row['currency'])} across {row['priced_orders']} "
            f"priced of {row['purchase_orders']} orders"
            for row in envelope.records
        )
        sentences = [
            f"{who} {totals} with {supplier.label} in {label} (cancelled orders excluded)."
        ]
        if len(envelope.records) > 1:
            sentences.append("Currencies are reported separately; no FX conversion is applied.")
        return sentences

    def _history(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        del observations, requested_action
        supplier = next(e for e in envelope.entities if e.entity_type == "Supplier")
        aliases = [f for f in envelope.facts if f.predicate == "alias"]
        citations.append(
            Citation(kind="record", ref="sql:purchases_for_supplier", label="ERP purchase orders")
        )
        sentences = [
            f"Found {len(envelope.records)} purchase orders for {supplier.label} in your scope"
            + (" (first page)." if "RESULTS_TRUNCATED" in envelope.warnings else ".")
        ]
        if aliases:
            sentences.append(
                f"Entity resolution combines {len(aliases)} source records for {supplier.label}: "
                + "; ".join(a.value for a in aliases)
                + "."
            )
        for row in envelope.records[:5]:
            amount = (
                _money(row["amount"], row["currency"]) if row.get("amount") else "amount missing"
            )
            recorded_as = row.get("recorded_supplier_name") or row["source_supplier_id"]
            sentences.append(
                f"{row['purchase_order_id']} on {row['ordered_on']}: {amount}, {row['status']}, "
                f"buyer {row['buyer_name']}, recorded as '{recorded_as}'."
            )
        return sentences

    def _buyers(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        del observations, requested_action
        supplier = next(e for e in envelope.entities if e.entity_type == "Supplier")
        citations.append(
            Citation(kind="record", ref="sql:buyers_for_supplier", label="ERP purchase orders")
        )
        if not envelope.records:
            return [f"No buyers in your scope purchased from {supplier.label}."]
        return [
            f"Buyers who purchased from {supplier.label}: "
            + "; ".join(
                f"{row['buyer_name']} ({row['purchase_orders']} orders, "
                f"{_money(row['total_amount'], row['currency'])})"
                for row in envelope.records[:10]
            )
            + "."
        ]

    def _entity_lookup(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        del observations, requested_action
        supplier = next(e for e in envelope.entities if e.entity_type == "Supplier")
        aliases = [f for f in envelope.facts if f.predicate.startswith("alias")]
        for alias in aliases:
            if alias.source_record_id:
                citations.append(
                    Citation(kind="fact", ref=alias.source_record_id, label=alias.value)
                )
        merged = [a.value for a in aliases if a.predicate == "alias"]
        review = [a.value for a in aliases if a.predicate == "alias_review_candidate"]
        sentences = [
            f"{supplier.label} is one canonical entity merged from {len(merged)} source records: "
            + "; ".join(merged)
            + "."
        ]
        if review:
            sentences.append("Awaiting manual review: " + "; ".join(review) + ".")
        if envelope.provenance:
            sentences.append(
                f"The graph records {len(envelope.provenance)} provenance assertions for it."
            )
        return sentences

    # -- graph routes ----------------------------------------------------------------------
    def _graph(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        del observations, requested_action
        template = envelope.classification.graph_template or ""
        citations.append(Citation(kind="graph", ref=f"template:{template}", label=template))
        rows = envelope.records
        if template == "suppliers_for_category_with_active_contracts":
            category = envelope.classification.category
            if not rows:
                return [f"No suppliers have active contracts covering {category}."]
            names = "; ".join(
                f"{row['supplierName']} "
                f"({str(row.get('status', '')).rsplit('#', 1)[-1] or 'status unknown'}, "
                f"{row['activeContracts']} active contract(s))"
                for row in rows[:15]
            )
            return [
                f"{len(rows)} suppliers have contracts active today that cover {category} "
                f"(including narrower categories): {names}."
            ]
        if template == "products_via_active_contracts":
            supplier = next(e for e in envelope.entities if e.entity_type == "Supplier")
            if not rows:
                return [f"No products are connected to {supplier.label} through active contracts."]
            categories = sorted({row["categoryLabel"] for row in rows})
            return [
                f"{len(rows)} products are connected to {supplier.label} through active contracts "
                f"covering {', '.join(categories)} (first 50 shown in the context)."
            ]
        if template == "is_business_partner":
            supplier = next(e for e in envelope.entities if e.entity_type == "Supplier")
            answer = rows[0].get("answer") if rows else None
            return [
                f"{'Yes' if answer else 'No'} - {supplier.label} is "
                f"{'' if answer else 'not '}an ecg:BusinessPartner. This is inferred: it is "
                "asserted as ecg:Supplier, and the ontology declares Supplier rdfs:subClassOf "
                "BusinessPartner."
            ]
        if template == "supplier_relationships":
            supplier = next(e for e in envelope.entities if e.entity_type == "Supplier")
            counts = ", ".join(
                f"{row['count']} {row['relation'].replace('_', ' ')}" for row in rows
            )
            return [f"{supplier.label} is linked to {counts} (within your scope)."]
        return [f"The graph query returned {len(rows)} rows."]

    def _graph_question(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        del observations, requested_action
        citations.append(Citation(kind="graph", ref="generated_sparql", label="validated query"))
        rows = envelope.records
        if not rows:
            return ["The graph query returned no results."]
        if len(rows) == 1 and len(rows[0]) == 1:
            key, value = next(iter(rows[0].items()))
            return [f"The answer is {value} ({key})."]
        preview = "; ".join(
            ", ".join(str(v).rsplit("/", 1)[-1] for v in row.values()) for row in rows[:10]
        )
        return [f"The graph returned {len(rows)} results: {preview}."]

    def _policy(
        self,
        envelope: ContextEnvelope,
        observations: dict[str, Any],
        citations: list[Citation],
        requested_action: str | None,
    ) -> list[str]:
        del observations, requested_action
        usable = [d for d in envelope.documents if not d.flagged_instructions]
        if not usable:
            return ["No authorized policy documents matched the question."]
        for document in usable[:3]:
            citations.append(
                Citation(kind="document", ref=document.document_id, label=document.title)
            )
        return [
            "From the policy documents: "
            + " ".join(f'{d.title} ({d.document_id}): "{d.snippet}"' for d in usable[:3])
        ]
