from collections.abc import Iterator, Sequence

import pytest
from fastapi.testclient import TestClient

from enterprise_context.api import app
from enterprise_context.retrieval.models import SearchHit, SearchRequest, SearchResponse
from enterprise_context.retrieval.opensearch import OpenSearchError
from enterprise_context.security.principals import PrincipalContext, get_current_principal


@pytest.fixture
def principal() -> PrincipalContext:
    return PrincipalContext(
        principal_id="user-alice",
        display_name="Alice Morgan",
        buyer_id="BUY-000",
        roles=["BUYER"],
        business_unit_ids=["BU-000"],
        approval_limit_minor=1_000_000,
    )


@pytest.fixture
def api_client(principal: PrincipalContext) -> Iterator[TestClient]:
    app.dependency_overrides[get_current_principal] = lambda: principal
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_search_route_passes_principal_filters_to_retriever(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class StubSearch:
        def search(
            self,
            request: SearchRequest,
            *,
            allowed_roles: Sequence[str],
            business_unit_ids: Sequence[str],
        ) -> SearchResponse:
            assert request.query == "blocked supplier policy"
            assert allowed_roles == ["BUYER"]
            assert business_unit_ids == ["BU-000"]
            return SearchResponse(
                query=request.query,
                hits=[
                    SearchHit(
                        document_id="POL-001",
                        title="Supplier status controls",
                        content="Blocked suppliers cannot receive purchase orders.",
                        document_type="PROCUREMENT_POLICY",
                        source_system="DOCUMENT_REPOSITORY",
                        source_record_id="DOC-001",
                        bm25_rank=1,
                        bm25_score=8.4,
                        vector_rank=2,
                        vector_score=0.82,
                        rrf_score=0.032,
                        rrf_rank=1,
                    )
                ],
            )

    monkeypatch.setattr("enterprise_context.api.get_search_retriever", lambda: StubSearch())
    response = api_client.post("/search", json={"query": "blocked supplier policy"})

    assert response.status_code == 200
    assert response.json()["hits"][0]["document_id"] == "POL-001"
    assert response.json()["hits"][0]["bm25_rank"] == 1


def test_search_service_failure_is_reported_as_unavailable(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UnavailableSearch:
        def search(
            self,
            request: SearchRequest,
            *,
            allowed_roles: Sequence[str],
            business_unit_ids: Sequence[str],
        ) -> SearchResponse:
            raise OpenSearchError("unavailable")

    monkeypatch.setattr("enterprise_context.api.get_search_retriever", lambda: UnavailableSearch())
    response = api_client.post("/search", json={"query": "policy"})

    assert response.status_code == 503
    assert response.json()["detail"] == "search_service_unavailable"
