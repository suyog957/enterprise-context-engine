from functools import lru_cache

from enterprise_context.config import get_settings
from enterprise_context.retrieval.embeddings import (
    EmbeddingProvider,
    FeatureHashEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from enterprise_context.retrieval.opensearch import OpenSearchHybridRetriever


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.embedding_provider == "feature_hash":
        return FeatureHashEmbeddingProvider(settings.embedding_dimensions)
    if settings.embedding_provider == "sentence_transformers":
        return SentenceTransformerEmbeddingProvider(
            settings.embedding_model,
            settings.embedding_dimensions,
        )
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


@lru_cache(maxsize=1)
def get_search_retriever() -> OpenSearchHybridRetriever:
    return OpenSearchHybridRetriever(
        get_settings().opensearch_url,
        get_embedding_provider(),
    )
