"""Build the real agent against live services, optionally with an injected fault.

Faults replace one dependency with an unreachable endpoint so the evaluation measures
real degradation behaviour (partial answers, fail-closed policy) end to end.
"""

from __future__ import annotations

from functools import lru_cache

from enterprise_context.agents.workflow import AgentWorkflow
from enterprise_context.config import get_settings
from enterprise_context.context_engine.classifier import IntentClassifier, taxonomy_labels
from enterprise_context.context_engine.engine import ContextEngine
from enterprise_context.domain.transactions import ProcurementTransactions
from enterprise_context.evaluation.chat_eval import Fault
from enterprise_context.graph.dependencies import get_graph_resolver
from enterprise_context.graph.query import FusekiGraphStore
from enterprise_context.retrieval.models import SearchRequest, SearchResponse
from enterprise_context.retrieval.opensearch import OpenSearchError
from enterprise_context.security.principals import PrincipalContext
from enterprise_context.tools.base import ToolRegistry
from enterprise_context.tools.catalog import build_tools
from enterprise_context.tools.dependencies import (
    build_tool_services,
    get_llm_provider,
    search_for_principal,
)

UNREACHABLE = "http://127.0.0.1:9"


def _search_down(request: SearchRequest, principal: PrincipalContext) -> SearchResponse:
    raise OpenSearchError("injected fault: search unavailable")


@lru_cache(maxsize=4)
def build_workflow(fault: Fault | None = None) -> AgentWorkflow:
    settings = get_settings()
    resolver = get_graph_resolver()
    graph_url = f"{UNREACHABLE}/enterprise" if fault == "graph_down" else settings.fuseki_url
    graph_store = FusekiGraphStore(graph_url, timeout_seconds=1.0, graph_uri_provider=resolver)
    transaction_settings = (
        settings.model_copy(update={"opa_url": UNREACHABLE}) if fault == "policy_down" else settings
    )
    services = build_tool_services(
        settings,
        graph_store=graph_store,
        search=_search_down if fault == "search_down" else search_for_principal,
        transactions=ProcurementTransactions(transaction_settings),
        llm=get_llm_provider(),
    )
    registry = ToolRegistry(build_tools(services))
    classifier = IntentClassifier(taxonomy_labels(settings.ontology_dir), llm=get_llm_provider())
    return AgentWorkflow(ContextEngine(registry, classifier), registry)
