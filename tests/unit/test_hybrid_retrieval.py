import json
from typing import Any

import httpx

from enterprise_context.retrieval.embeddings import FeatureHashEmbeddingProvider
from enterprise_context.retrieval.models import SearchRequest
from enterprise_context.retrieval.opensearch import OpenSearchHybridRetriever, authorization_filters
from enterprise_context.retrieval.rrf import RankedDocument, reciprocal_rank_fusion


def test_rrf_combines_lexical_and_vector_rankings_with_explanations() -> None:
    lexical = [
        RankedDocument("policy-a", {"title": "A"}, rank=1, score=12.0),
        RankedDocument("policy-b", {"title": "B"}, rank=2, score=8.0),
    ]
    semantic = [
        RankedDocument("policy-b", {"title": "B"}, rank=1, score=0.91),
        RankedDocument("policy-c", {"title": "C"}, rank=2, score=0.84),
    ]

    result = reciprocal_rank_fusion(lexical, semantic, rank_constant=60, limit=3)

    assert [item.document_id for item in result] == ["policy-b", "policy-a", "policy-c"]
    assert result[0].bm25_rank == 2
    assert result[0].vector_rank == 1
    assert result[0].bm25_score == 8.0
    assert result[0].vector_score == 0.91


def test_authorization_filters_deny_empty_roles_and_scope_business_units() -> None:
    assert authorization_filters([], ["BU-000"])[0] == {"term": {"_id": "__deny_all__"}}
    filters = authorization_filters(["BUYER"], ["BU-000"])

    assert {"terms": {"acl_roles": ["BUYER"]}} in filters
    assert filters[1]["bool"]["should"][0] == {"terms": {"business_unit_ids": ["BU-000"]}}


def test_hybrid_search_applies_auth_filters_and_returns_rank_explanations() -> None:
    calls: list[dict[str, Any]] = []

    def hit(document_id: str, title: str, content: str) -> dict[str, Any]:
        return {
            "_id": document_id,
            "_score": 1.0,
            "_source": {
                "title": title,
                "content": content,
                "document_type": "POLICY",
                "source_system": "DOCS",
                "source_record_id": document_id.upper(),
            },
        }

    def handle_request(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        serialized = json.dumps(body)
        assert '"acl_roles"' in serialized
        assert "BUYER" in serialized
        assert "BU-000" in serialized
        if "multi_match" in serialized:
            hits = [
                hit("doc-a", "A", "Alpha"),
                hit("doc-b", "B", "Beta"),
            ]
        else:
            hits = [
                hit("doc-b", "B", "Beta"),
                hit("doc-c", "C", "Gamma"),
            ]
        return httpx.Response(200, json={"hits": {"hits": hits}})

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        search = OpenSearchHybridRetriever(
            "http://opensearch:9200",
            FeatureHashEmbeddingProvider(32),
            client=client,
        )
        response = search.search(
            SearchRequest(query="blocked suppliers", document_types=["POLICY"], limit=3),
            allowed_roles=["BUYER"],
            business_unit_ids=["BU-000"],
        )

    assert len(calls) == 2
    assert response.hits[0].document_id == "doc-b"
    assert response.hits[0].bm25_rank == 2
    assert response.hits[0].vector_rank == 1
    assert response.hits[0].rrf_rank == 1
