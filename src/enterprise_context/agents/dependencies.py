from functools import lru_cache

from enterprise_context.agents.procurement import ProcurementAgent
from enterprise_context.config import get_settings
from enterprise_context.domain.requisitions import (
    RequisitionContext,
    get_authorized_requisition_context,
)
from enterprise_context.domain.transaction_dependencies import get_procurement_transactions
from enterprise_context.graph.access import FusekiContextReader
from enterprise_context.graph.query import FusekiGraphStore, GraphQueryError
from enterprise_context.policy.models import PolicyDecision
from enterprise_context.retrieval.dependencies import get_search_retriever
from enterprise_context.retrieval.models import SearchHit, SearchRequest
from enterprise_context.retrieval.opensearch import OpenSearchError
from enterprise_context.security.principals import PrincipalContext


@lru_cache(maxsize=1)
def get_procurement_agent() -> ProcurementAgent:
    settings = get_settings()
    graph_reader = FusekiContextReader(FusekiGraphStore(settings.fuseki_url))

    def load_context(requisition_id: str, principal: PrincipalContext) -> RequisitionContext | None:
        context = get_authorized_requisition_context(requisition_id, principal)
        if context is None:
            return None
        try:
            graph_facts = graph_reader.get_requisition_facts(requisition_id)
        except GraphQueryError:
            return context.model_copy(update={"graph_available": False})
        return context.model_copy(update={"graph_facts": graph_facts, "graph_available": True})

    def evaluate_policy(context: RequisitionContext, principal: PrincipalContext) -> PolicyDecision:
        return get_procurement_transactions().evaluate_action(context, principal)

    def search_supporting_documents(
        context: RequisitionContext, principal: PrincipalContext
    ) -> list[SearchHit]:
        query = (
            f"purchase order requisition {context.state} supplier {context.supplier.status} "
            f"risk {context.supplier.risk_rating} approval limits "
            f"{' '.join(context.categories)}"
        )
        try:
            response = get_search_retriever().search(
                SearchRequest(
                    query=query,
                    document_types=["PROCUREMENT_POLICY", "CONTRACT"],
                    limit=5,
                ),
                allowed_roles=principal.roles,
                business_unit_ids=principal.business_unit_ids,
            )
        except OpenSearchError:
            return []
        return response.hits

    return ProcurementAgent(
        context_loader=load_context,
        decision_evaluator=evaluate_policy,
        document_searcher=search_supporting_documents,
    )
