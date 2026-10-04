from functools import lru_cache

from enterprise_context.config import get_settings
from enterprise_context.context_engine.classifier import IntentClassifier, taxonomy_labels
from enterprise_context.context_engine.engine import ContextEngine
from enterprise_context.graph.dependencies import get_graph_resolver
from enterprise_context.tools.dependencies import get_llm_provider, get_tool_registry


def current_graph_version() -> str | None:
    version = get_graph_resolver().current()
    if version is None:
        return None
    freshness = f"{version.target_uri} (published {version.published_at.isoformat()}"
    if version.source_watermark is not None:
        freshness += f"; changes applied through {version.source_watermark.isoformat()}"
    return freshness + ")"


@lru_cache(maxsize=1)
def get_context_engine() -> ContextEngine:
    settings = get_settings()
    classifier = IntentClassifier(taxonomy_labels(settings.ontology_dir), llm=get_llm_provider())
    return ContextEngine(get_tool_registry(), classifier, graph_version=current_graph_version)
