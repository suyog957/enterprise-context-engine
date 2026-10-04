"""Retrieval quality per embedding provider and ranker (lexical, vector, hybrid RRF)."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel

from enterprise_context.evaluation.ranking import (
    mean,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from enterprise_context.retrieval.embeddings import EmbeddingProvider
from enterprise_context.retrieval.models import IndexedDocument, SearchRequest
from enterprise_context.retrieval.opensearch import OpenSearchHybridRetriever, SearchMode

EVALUATION_ROLES = ["ADMIN", "AUDITOR", "BUYER", "MANAGER"]
MODES: tuple[SearchMode, ...] = ("lexical", "vector", "hybrid")


class RetrievalQuery(BaseModel):
    query_id: str
    query: str
    relevant: list[str]
    kind: str
    document_types: list[str] = []


def score_rankings(
    queries: Sequence[RetrievalQuery],
    rankings: dict[str, list[str]],
    latencies_ms: Sequence[float],
) -> dict[str, Any]:
    per_kind: dict[str, list[float]] = defaultdict(list)
    recall5, recall10, precision5, mrr, ndcg = [], [], [], [], []
    misses = []
    for query in queries:
        ranked = rankings.get(query.query_id, [])
        relevant = set(query.relevant)
        recall5.append(recall_at_k(ranked, relevant, 5))
        recall10.append(recall_at_k(ranked, relevant, 10))
        precision5.append(precision_at_k(ranked, relevant, 5))
        mrr.append(reciprocal_rank(ranked, relevant))
        ndcg.append(ndcg_at_k(ranked, relevant, 10))
        per_kind[query.kind].append(recall10[-1])
        if recall10[-1] < 1.0:
            misses.append({"query_id": query.query_id, "query": query.query, "top3": ranked[:3]})
    return {
        "queries": len(queries),
        "recall_at_5": round(mean(recall5), 4),
        "recall_at_10": round(mean(recall10), 4),
        "precision_at_5": round(mean(precision5), 4),
        "mrr": round(mean(mrr), 4),
        "ndcg_at_10": round(mean(ndcg), 4),
        "recall_at_10_by_kind": {k: round(mean(v), 4) for k, v in sorted(per_kind.items())},
        "latency_p50_ms": round(percentile(latencies_ms, 0.5), 2),
        "latency_p95_ms": round(percentile(latencies_ms, 0.95), 2),
        "misses": misses,
    }


def _index_size_bytes(base_url: str, index: str) -> int | None:
    try:
        response = httpx.get(
            f"{base_url}/_cat/indices/{index}", params={"format": "json", "bytes": "b"}, timeout=5
        )
        response.raise_for_status()
        return int(response.json()[0]["store.size"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


def evaluate_provider(
    base_url: str,
    label: str,
    embedder: EmbeddingProvider,
    documents: Sequence[IndexedDocument],
    queries: Sequence[RetrievalQuery],
) -> dict[str, Any]:
    """Index into a throwaway index, measure every ranker, then delete the index."""
    index = "eval-retrieval-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    retriever = OpenSearchHybridRetriever(base_url, embedder, index_name=index)
    retriever.delete_index(index)
    retriever.ensure_index(index)
    started = time.perf_counter()
    retriever.index_documents(documents, index)
    indexing_ms = (time.perf_counter() - started) * 1000
    try:
        results: dict[str, Any] = {
            "provider": label,
            "dimensions": embedder.dimensions,
            "documents": len(documents),
            "indexing_ms": round(indexing_ms, 1),
            "index_size_bytes": _index_size_bytes(base_url, index),
        }
        for mode in MODES:
            rankings: dict[str, list[str]] = {}
            latencies: list[float] = []
            for query in queries:
                query_started = time.perf_counter()
                response = retriever.search(
                    SearchRequest(query=query.query, limit=10),
                    allowed_roles=EVALUATION_ROLES,
                    business_unit_ids=[],
                    mode=mode,
                )
                latencies.append((time.perf_counter() - query_started) * 1000)
                rankings[query.query_id] = [hit.document_id for hit in response.hits]
            results[mode] = score_rankings(queries, rankings, latencies)
        return results
    finally:
        retriever.delete_index(index)
