"""Hybrid retrieval, ACL filtering and alias switching against a live OpenSearch."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest

from enterprise_context.retrieval.embeddings import FeatureHashEmbeddingProvider
from enterprise_context.retrieval.models import IndexedDocument, SearchRequest
from enterprise_context.retrieval.opensearch import OpenSearchHybridRetriever

pytestmark = pytest.mark.integration


def document(document_id: str, content: str, roles: list[str], units: list[str]) -> IndexedDocument:
    return IndexedDocument(
        document_id=document_id,
        title=f"Policy {document_id}",
        content=content,
        document_type="PROCUREMENT_POLICY",
        source_system="DOCUMENT_REPOSITORY",
        source_record_id=f"SRC-{document_id}",
        acl_roles=roles,
        business_unit_ids=units,
    )


@pytest.fixture
def retriever() -> Iterator[OpenSearchHybridRetriever]:
    url = os.environ.get("OPENSEARCH_URL")
    if not url or not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("OPENSEARCH_URL and TEST_DATABASE_URL are required")
    alias = f"itest-{uuid4().hex[:8]}"
    store = OpenSearchHybridRetriever(url, FeatureHashEmbeddingProvider(64), index_name=alias)
    yield store
    for index in store.list_versioned_indices():
        store.delete_index(index)


def test_hybrid_search_filters_by_acl_and_business_unit_before_returning(
    retriever: OpenSearchHybridRetriever,
) -> None:
    index = f"{retriever.versioned_prefix}1"
    retriever.ensure_index(index)
    retriever.index_documents(
        [
            document("open", "blocked supplier purchase order rules", ["BUYER"], []),
            document("audit", "blocked supplier audit findings", ["AUDITOR"], []),
            document("bu9", "blocked supplier exception for unit nine", ["BUYER"], ["BU-009"]),
        ],
        index,
    )
    retriever.switch_alias(index)

    buyer = retriever.search(
        SearchRequest(query="blocked supplier", limit=10),
        allowed_roles=["BUYER"],
        business_unit_ids=["BU-000"],
    )
    auditor = retriever.search(
        SearchRequest(query="blocked supplier", limit=10),
        allowed_roles=["AUDITOR"],
        business_unit_ids=[],
    )
    nobody = retriever.search(
        SearchRequest(query="blocked supplier"), allowed_roles=[], business_unit_ids=[]
    )

    assert {hit.document_id for hit in buyer.hits} == {"open"}
    assert {hit.document_id for hit in auditor.hits} == {"audit"}
    assert nobody.hits == []
    top = buyer.hits[0]
    assert top.rrf_rank == 1 and top.bm25_rank == 1 and top.vector_rank == 1


def test_alias_switch_moves_readers_to_the_new_version(
    retriever: OpenSearchHybridRetriever,
) -> None:
    first, second = f"{retriever.versioned_prefix}1", f"{retriever.versioned_prefix}2"
    for index, text in ((first, "old approval wording"), (second, "new approval wording")):
        retriever.ensure_index(index)
        retriever.index_documents([document(index[-1], text, ["BUYER"], [])], index)
    retriever.switch_alias(first)
    retired = retriever.switch_alias(second)

    hits = retriever.search(
        SearchRequest(query="approval wording"), allowed_roles=["BUYER"], business_unit_ids=[]
    ).hits
    assert retired == [first]
    assert [hit.document_id for hit in hits] == ["2"]
