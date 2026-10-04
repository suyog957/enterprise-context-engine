from functools import lru_cache

from enterprise_context.config import get_settings
from enterprise_context.graph.projection import CurrentGraphResolver
from enterprise_context.graph.query import FusekiGraphStore


@lru_cache(maxsize=1)
def get_graph_resolver() -> CurrentGraphResolver:
    return CurrentGraphResolver(get_settings().database_url)


@lru_cache(maxsize=1)
def get_graph_store() -> FusekiGraphStore:
    """Read-only graph store scoped to the currently published projection."""
    settings = get_settings()
    return FusekiGraphStore(settings.fuseki_url, graph_uri_provider=get_graph_resolver())
