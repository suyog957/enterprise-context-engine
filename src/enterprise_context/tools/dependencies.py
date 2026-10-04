from collections.abc import Callable
from functools import lru_cache

from enterprise_context.config import Settings, get_settings
from enterprise_context.domain.entities import get_entity_context, resolve_entities
from enterprise_context.domain.requisitions import (
    RequisitionContext,
    get_authorized_requisition_context,
)
from enterprise_context.domain.transaction_dependencies import get_procurement_transactions
from enterprise_context.domain.transactions import ProcurementTransactions
from enterprise_context.graph.access import FusekiContextReader
from enterprise_context.graph.dependencies import get_graph_store
from enterprise_context.graph.nl2sparql import NaturalLanguageGraphQuery
from enterprise_context.graph.query import FusekiGraphStore, GraphQueryError
from enterprise_context.graph.templates import GraphTemplateService
from enterprise_context.llm.providers import LLMProvider, build_llm_provider
from enterprise_context.retrieval.dependencies import get_search_retriever
from enterprise_context.retrieval.models import SearchRequest, SearchResponse
from enterprise_context.security.principals import PrincipalContext
from enterprise_context.tools.base import ToolRegistry
from enterprise_context.tools.catalog import ToolServices, build_tools
from enterprise_context.tools.sql_catalog import SqlCatalog

Searcher = Callable[[SearchRequest, PrincipalContext], SearchResponse]


def requisition_loader(
    graph_store: FusekiGraphStore,
) -> Callable[[str, PrincipalContext], RequisitionContext | None]:
    def load(requisition_id: str, principal: PrincipalContext) -> RequisitionContext | None:
        """Authorized transactional facts first; graph facts only for an authorized resource."""
        context = get_authorized_requisition_context(requisition_id, principal)
        if context is None:
            return None
        try:
            facts = FusekiContextReader(graph_store).get_requisition_facts(requisition_id)
        except GraphQueryError:
            return context.model_copy(update={"graph_available": False})
        return context.model_copy(update={"graph_facts": facts, "graph_available": True})

    return load


def search_for_principal(request: SearchRequest, principal: PrincipalContext) -> SearchResponse:
    return get_search_retriever().search(
        request, allowed_roles=principal.roles, business_unit_ids=principal.business_unit_ids
    )


def build_tool_services(
    settings: Settings,
    *,
    graph_store: FusekiGraphStore,
    search: Searcher,
    transactions: ProcurementTransactions,
    llm: LLMProvider,
) -> ToolServices:
    """Assemble tool services from explicit components (the evaluator swaps in faults)."""
    return ToolServices(
        resolve_entities=resolve_entities,
        search=search,
        entity_context=get_entity_context,
        requisition_context=requisition_loader(graph_store),
        action_decisions=transactions.evaluate_action_catalog,
        simulate=transactions.simulate,
        graph_templates=GraphTemplateService(graph_store),
        sql=SqlCatalog.from_url(settings.database_url),
        nl_graph=NaturalLanguageGraphQuery(graph_store, llm, ontology_dir=settings.ontology_dir),
        dry_run=settings.dry_run,
    )


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    return build_llm_provider(get_settings())


@lru_cache(maxsize=1)
def get_graph_template_service() -> GraphTemplateService:
    return GraphTemplateService(get_graph_store())


@lru_cache(maxsize=1)
def get_sql_catalog() -> SqlCatalog:
    return SqlCatalog.from_url(get_settings().database_url)


@lru_cache(maxsize=1)
def get_nl_graph_query() -> NaturalLanguageGraphQuery:
    return NaturalLanguageGraphQuery(
        get_graph_store(), get_llm_provider(), ontology_dir=get_settings().ontology_dir
    )


@lru_cache(maxsize=1)
def get_tool_services() -> ToolServices:
    return build_tool_services(
        get_settings(),
        graph_store=get_graph_store(),
        search=search_for_principal,
        transactions=get_procurement_transactions(),
        llm=get_llm_provider(),
    )


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    return ToolRegistry(build_tools(get_tool_services()))
