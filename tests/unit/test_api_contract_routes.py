from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from enterprise_context.api import app
from enterprise_context.domain.entities import (
    EntityAlias,
    EntityCandidate,
    EntityContextResponse,
    EntityProvenance,
    EntityResolveRequest,
    EntityResolveResponse,
)
from enterprise_context.graph.query import GraphQueryResult
from enterprise_context.security.principals import PrincipalContext, get_current_principal


@pytest.fixture
def principal() -> PrincipalContext:
    return PrincipalContext(
        principal_id="user-admin",
        display_name="Admin",
        buyer_id=None,
        roles=["ADMIN", "AUDITOR"],
        business_unit_ids=["BU-000"],
        approval_limit_minor=10_000_000,
    )


@pytest.fixture
def api_client(principal: PrincipalContext) -> Iterator[TestClient]:
    app.dependency_overrides[get_current_principal] = lambda: principal
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def entity_context() -> EntityContextResponse:
    return EntityContextResponse(
        entity=EntityCandidate(
            canonical_entity_id="supplier-acme",
            preferred_name="Acme Corp",
            status="ACTIVE",
            risk_rating="LOW",
        ),
        aliases=[
            EntityAlias(
                source_system="ERP",
                source_supplier_id="SUP-0000",
                source_record_id="ERP-SUP-0000",
                alias="ACME Corporation",
                resolution_method="exact_tax_id",
                confidence_score=1.0,
                review_required=False,
            )
        ],
        related_requisition_ids=["PR-1007"],
        related_purchase_order_ids=[],
        active_contract_ids=["CON-2001"],
        provenance=[
            EntityProvenance(
                source_system="ERP",
                source_record_id="ERP-SUP-0000",
                source_supplier_id="SUP-0000",
                fact="Alias 'ACME Corporation' resolved by exact_tax_id",
            )
        ],
    )


def test_entity_resolution_route_returns_authorized_candidates(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def resolve_stub(
        request: EntityResolveRequest, principal: PrincipalContext
    ) -> EntityResolveResponse:
        assert request.query == "Acme"
        assert principal.principal_id == "user-admin"
        return EntityResolveResponse(query=request.query, candidates=[entity_context().entity])

    monkeypatch.setattr("enterprise_context.api.resolve_entities", resolve_stub)

    response = api_client.post("/entities/resolve", json={"query": "Acme"})

    assert response.status_code == 200
    assert response.json()["candidates"][0]["canonical_entity_id"] == "supplier-acme"


def test_entity_context_route_maps_missing_entity_to_not_found(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "enterprise_context.api.get_entity_context",
        lambda entity_id, principal: None,
    )

    response = api_client.get("/entities/missing/context")

    assert response.status_code == 404
    assert response.json()["detail"] == "entity_not_found"


def test_graph_query_route_uses_bounded_fuseki_store(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class StubStore:
        def run_readonly_sparql(self, query: str) -> GraphQueryResult:
            assert "LIMIT 1" in query
            return GraphQueryResult(query_type="SelectQuery", rows=[{"s": "urn:acme"}])

    monkeypatch.setattr("enterprise_context.api.get_graph_store", lambda: StubStore())

    response = api_client.post(
        "/graph/query",
        json={"query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"},
    )

    assert response.status_code == 200
    assert response.json()["rows"] == [{"s": "urn:acme"}]


def test_latest_evaluation_route_reads_generated_report(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report_dir = tmp_path / "data" / "evaluation" / "generated"
    report_dir.mkdir(parents=True)
    (report_dir / "policy_latest.json").write_text(
        '{"metrics":{"policy_violation_rate":0}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("enterprise_context.api.settings.data_dir", tmp_path / "data")

    response = api_client.get("/evaluation/latest")

    assert response.status_code == 200
    assert response.json()["metrics"]["policy_violation_rate"] == 0
