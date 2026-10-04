from functools import lru_cache

from enterprise_context.config import get_settings
from enterprise_context.domain.entities import get_entity_context, resolve_entities
from enterprise_context.domain.requisitions import (
    RequisitionContext,
    get_authorized_requisition_context,
)
from enterprise_context.domain.transaction_dependencies import get_procurement_transactions
from enterprise_context.graph.access import FusekiContextReader
from enterprise_context.graph.dependencies import get_graph_store
from enterprise_context.graph.nl2sparql import NaturalLanguageGraphQuery
from enterprise_context.graph.query import GraphQueryError
from enterprise_context.graph.templates import GraphTemplateService
from enterprise_context.llm.providers import LLMProvider, build_llm_provider
from enterprise_context.retrieval.dependencies import get_search_retriever
from enterprise_context.retrieval.models import SearchRequest, SearchResponse
from enterprise_context.security.principals import PrincipalContext
from enterprise_context.tools.base import ToolRegistry
from enterprise_context.tools.catalog import ToolServices, build_tools
from enterprise_context.tools.sql_catalog import SqlCatalog


def load_requisition_with_graph(
    requisition_id: str, principal: PrincipalContext
) -> RequisitionContext | None:
    """Authorized transactional facts first; graph facts only for an authorized resource."""
    context = get_authorized_requisition_context(requisition_id, principal)
    if context is None:
        return None
    try:
        graph_facts = FusekiContextReader(get_graph_store()).get_requisition_facts(requisition_id)
    except GraphQueryError:
        return context.model_copy(update={"graph_available": False})
    return context.model_copy(update={"graph_facts": graph_facts, "graph_available": True})


def search_for_principal(request: SearchRequest, principal: PrincipalContext) -> SearchResponse:
    return get_search_retriever().search(
        request, allowed_roles=principal.roles, business_unit_ids=principal.business_unit_ids
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
    transactions = get_procurement_transactions()
    return ToolServices(
        resolve_entities=resolve_entities,
        search=search_for_principal,
        entity_context=get_entity_context,
        requisition_context=load_requisition_with_graph,
        action_decisions=transactions.evaluate_action_catalog,
        simulate=transactions.simulate,
        graph_templates=get_graph_template_service(),
        sql=get_sql_catalog(),
        nl_graph=get_nl_graph_query(),
        dry_run=get_settings().dry_run,
    )


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    return ToolRegistry(build_tools(get_tool_services()))
