from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import httpx

from enterprise_context.observability.tracing import observed_store_call
from enterprise_context.retrieval.embeddings import EmbeddingProvider
from enterprise_context.retrieval.models import (
    IndexedDocument,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from enterprise_context.retrieval.rrf import RankedDocument, reciprocal_rank_fusion

SEARCH_ALIAS = "enterprise-context-documents"
SearchMode = Literal["hybrid", "lexical", "vector"]
VERSIONED_INDEX_PREFIX = f"{SEARCH_ALIAS}-v"


class OpenSearchError(RuntimeError):
    """Raised when indexing or hybrid search cannot be completed."""


def authorization_filters(
    roles: Sequence[str], business_unit_ids: Sequence[str]
) -> list[dict[str, Any]]:
    if not roles:
        return [{"term": {"_id": "__deny_all__"}}]
    filters: list[dict[str, Any]] = [{"terms": {"acl_roles": list(roles)}}]
    if business_unit_ids:
        filters.append(
            {
                "bool": {
                    "should": [
                        {"terms": {"business_unit_ids": list(business_unit_ids)}},
                        {"bool": {"must_not": [{"exists": {"field": "business_unit_ids"}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    return filters


def _ranked_documents(payload: Mapping[str, Any]) -> list[RankedDocument]:
    hits = payload.get("hits", {}).get("hits", [])
    return [
        RankedDocument(
            document_id=str(hit["_id"]),
            source=hit.get("_source", {}),
            rank=rank,
            score=float(hit.get("_score") or 0),
        )
        for rank, hit in enumerate(hits, start=1)
    ]


class OpenSearchHybridRetriever:
    def __init__(
        self,
        base_url: str,
        embedder: EmbeddingProvider,
        *,
        index_name: str = SEARCH_ALIAS,
        timeout_seconds: float = 4.0,
        rank_constant: int = 60,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._embedder = embedder
        self._index_name = index_name
        self._timeout_seconds = timeout_seconds
        self._rank_constant = rank_constant
        self._client = client

    def ensure_index(self, index_name: str | None = None) -> None:
        index_name = index_name or self._index_name
        client, owns_client = self._get_client()
        try:
            response = client.head(f"{self._base_url}/{index_name}")
            if response.status_code == 200:
                return
            if response.status_code != 404:
                response.raise_for_status()
            mapping = {
                "settings": {"index": {"knn": True}},
                "mappings": {
                    "properties": {
                        "title": {"type": "text"},
                        "content": {"type": "text"},
                        "document_type": {"type": "keyword"},
                        "source_system": {"type": "keyword"},
                        "source_record_id": {"type": "keyword"},
                        "acl_roles": {"type": "keyword"},
                        "business_unit_ids": {"type": "keyword"},
                        "effective_from": {
                            "type": "date",
                            "format": "strict_date_optional_time||yyyy-MM-dd",
                        },
                        "effective_to": {
                            "type": "date",
                            "format": "strict_date_optional_time||yyyy-MM-dd",
                        },
                        "embedding": {
                            "type": "knn_vector",
                            "dimension": self._embedder.dimensions,
                            "method": {
                                "name": "hnsw",
                                "space_type": "cosinesimil",
                                "engine": "lucene",
                            },
                        },
                    }
                },
            }
            created = client.put(f"{self._base_url}/{index_name}", json=mapping)
            created.raise_for_status()
        except httpx.HTTPError as error:
            raise OpenSearchError("Unable to create or inspect the OpenSearch index") from error
        finally:
            if owns_client:
                client.close()

    def index_documents(
        self, documents: Sequence[IndexedDocument], index_name: str | None = None
    ) -> int:
        index_name = index_name or self._index_name
        if not documents:
            return 0
        vectors = self._embedder.embed([f"{doc.title}\n{doc.content}" for doc in documents])
        lines: list[str] = []
        for document, vector in zip(documents, vectors, strict=True):
            lines.append(
                json.dumps(
                    {"index": {"_index": index_name, "_id": document.document_id}},
                    separators=(",", ":"),
                )
            )
            source = document.model_dump(mode="json")
            source["embedding"] = vector
            lines.append(json.dumps(source, separators=(",", ":")))
        payload = "\n".join(lines) + "\n"
        client, owns_client = self._get_client()
        try:
            response = client.post(
                f"{self._base_url}/_bulk",
                params={"refresh": "true"},
                content=payload,
                headers={"Content-Type": "application/x-ndjson"},
            )
            response.raise_for_status()
            result = response.json()
            if result.get("errors"):
                raise OpenSearchError("OpenSearch reported document indexing errors")
            return len(documents)
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, OpenSearchError):
                raise
            raise OpenSearchError("Unable to index documents") from error
        finally:
            if owns_client:
                client.close()

    def search(
        self,
        request: SearchRequest,
        *,
        allowed_roles: Sequence[str],
        business_unit_ids: Sequence[str],
        mode: SearchMode = "hybrid",
    ) -> SearchResponse:
        """Hybrid BM25 + k-NN with RRF; ``mode`` isolates one ranker for evaluation."""
        filters = authorization_filters(allowed_roles, business_unit_ids)
        if not allowed_roles:
            return SearchResponse(query=request.query, hits=[])
        filters.extend({"terms": {field: values}} for field, values in (
            ("document_type", request.document_types),
            ("source_system", request.source_systems),
        ) if values)

        lexical_query = {
            "size": request.limit,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": request.query,
                                "fields": ["title^2", "content"],
                                "type": "best_fields",
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
        }
        vector = self._embedder.embed([request.query])[0]
        vector_query = {
            "size": request.limit,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": vector,
                        "k": request.limit,
                        "filter": {"bool": {"filter": filters}},
                    }
                }
            },
        }

        client, owns_client = self._get_client()
        try:
            index_attributes = {"ecg.index": self._index_name}
            with observed_store_call("opensearch", "bm25_search", **index_attributes):
                lexical_response = client.post(
                    f"{self._base_url}/{self._index_name}/_search", json=lexical_query
                )
                lexical_response.raise_for_status()
            with observed_store_call("opensearch", "knn_search", **index_attributes):
                vector_response = client.post(
                    f"{self._base_url}/{self._index_name}/_search", json=vector_query
                )
                vector_response.raise_for_status()
            lexical = _ranked_documents(lexical_response.json())
            semantic = _ranked_documents(vector_response.json())
            fused = reciprocal_rank_fusion(
                lexical if mode != "vector" else [],
                semantic if mode != "lexical" else [],
                rank_constant=self._rank_constant,
                limit=request.limit,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, OpenSearchError):
                raise
            raise OpenSearchError("Hybrid document retrieval failed") from error
        finally:
            if owns_client:
                client.close()

        hits = [
            SearchHit(
                document_id=item.document_id,
                title=str(item.source.get("title", "")),
                content=str(item.source.get("content", "")),
                document_type=str(item.source.get("document_type", "")),
                source_system=str(item.source.get("source_system", "")),
                source_record_id=str(item.source.get("source_record_id", "")),
                bm25_rank=item.bm25_rank,
                bm25_score=item.bm25_score,
                vector_rank=item.vector_rank,
                vector_score=item.vector_score,
                rrf_score=item.score,
                rrf_rank=rank,
            )
            for rank, item in enumerate(fused, start=1)
        ]
        return SearchResponse(query=request.query, hits=hits)

    def list_versioned_indices(self) -> list[str]:
        client, owns_client = self._get_client()
        try:
            response = client.get(
                f"{self._base_url}/_cat/indices/{VERSIONED_INDEX_PREFIX}*",
                params={"format": "json"},
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return sorted(str(row["index"]) for row in response.json())
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise OpenSearchError("Unable to list search indices") from error
        finally:
            if owns_client:
                client.close()

    def switch_alias(self, new_index: str) -> list[str]:
        """Atomically point the search alias at ``new_index``; return retired indices."""
        retired = [index for index in self.list_versioned_indices() if index != new_index]
        client, owns_client = self._get_client()
        try:
            current = client.get(f"{self._base_url}/_alias/{self._index_name}")
            members = list(current.json()) if current.status_code == 200 else []
            actions: list[dict[str, Any]] = [
                {"remove": {"index": index, "alias": self._index_name}}
                for index in members
                if index != new_index
            ]
            actions.append({"add": {"index": new_index, "alias": self._index_name}})
            response = client.post(f"{self._base_url}/_aliases", json={"actions": actions})
            response.raise_for_status()
            return retired
        except (httpx.HTTPError, ValueError) as error:
            raise OpenSearchError("Unable to switch the search alias") from error
        finally:
            if owns_client:
                client.close()

    def delete_index(self, index_name: str) -> None:
        client, owns_client = self._get_client()
        try:
            response = client.delete(f"{self._base_url}/{index_name}")
            if response.status_code != 404:
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise OpenSearchError(f"Unable to delete index {index_name}") from error
        finally:
            if owns_client:
                client.close()

    def _get_client(self) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.Client(timeout=self._timeout_seconds), True
