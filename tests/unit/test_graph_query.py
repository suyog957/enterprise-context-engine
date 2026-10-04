from urllib.parse import parse_qs

import httpx
import pytest

from enterprise_context.graph.query import (
    FusekiGraphStore,
    GraphQueryError,
    GraphQueryValidationError,
    validate_readonly_sparql,
)


def test_select_queries_require_a_bounded_limit() -> None:
    assert validate_readonly_sparql("SELECT ?s WHERE { ?s ?p ?o } LIMIT 20") == (
        "SelectQuery",
        20,
    )
    with pytest.raises(GraphQueryValidationError, match="must specify a LIMIT"):
        validate_readonly_sparql("SELECT ?s WHERE { ?s ?p ?o }")
    with pytest.raises(GraphQueryValidationError, match="maximum"):
        validate_readonly_sparql("SELECT ?s WHERE { ?s ?p ?o } LIMIT 1000")


def test_only_select_and_ask_without_remote_services_are_allowed() -> None:
    assert validate_readonly_sparql("ASK { <urn:subject> ?p ?o }")[0] == "AskQuery"
    forbidden = [
        "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
        "SELECT ?s WHERE { SERVICE <https://remote.example/sparql> { ?s ?p ?o } } LIMIT 10",
        "SELECT ?s FROM <https://remote.example/graph> WHERE { ?s ?p ?o } LIMIT 10",
    ]
    for query in forbidden:
        with pytest.raises(GraphQueryValidationError):
            validate_readonly_sparql(query)


def test_fuseki_client_executes_query_with_timeout_and_flattens_results() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/enterprise/query"
        assert request.url.params["timeout"] == "3000"
        assert "LIMIT 2" in parse_qs(request.content.decode())["query"][0]
        return httpx.Response(
            200,
            json={
                "head": {"vars": ["supplier"]},
                "results": {
                    "bindings": [
                        {"supplier": {"type": "uri", "value": "urn:acme"}},
                        {"supplier": {"type": "uri", "value": "urn:other"}},
                    ]
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        store = FusekiGraphStore("http://fuseki:3030/enterprise", client=client)
        result = store.run_readonly_sparql(
            "SELECT ?supplier WHERE { ?supplier ?p ?o } LIMIT 2"
        )

    assert result.query_type == "SelectQuery"
    assert result.rows == [{"supplier": "urn:acme"}, {"supplier": "urn:other"}]


def test_fuseki_client_returns_ask_boolean() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"head": {}, "boolean": True})

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        store = FusekiGraphStore("http://fuseki:3030/enterprise", client=client)
        result = store.run_readonly_sparql(
            "ASK { <urn:acme> ?p ?o }"
        )

    assert result.boolean is True


def test_fuseki_client_uploads_only_the_validated_turtle_graph() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/enterprise/data"
        assert "default" in request.url.params
        assert request.headers["content-type"] == "text/turtle"
        assert request.headers["authorization"].startswith("Basic ")
        assert b"@prefix" in request.content
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        FusekiGraphStore(
            "http://fuseki:3030/enterprise",
            admin_user="admin",
            admin_password="local-password",
            client=client,
        ).replace_default_graph(
            b"@prefix ex: <urn:example:> . ex:s ex:p ex:o ."
        )


def test_mutations_and_graph_escapes_are_rejected_with_specific_reasons() -> None:
    with pytest.raises(GraphQueryValidationError, match="Mutation"):
        validate_readonly_sparql("DELETE WHERE { ?s ?p ?o }")
    with pytest.raises(GraphQueryValidationError, match="Mutation"):
        validate_readonly_sparql("INSERT DATA { <urn:a> <urn:b> <urn:c> }")
    with pytest.raises(GraphQueryValidationError, match="GRAPH patterns"):
        validate_readonly_sparql("SELECT ?s WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 5")
    with pytest.raises(GraphQueryValidationError, match="syntax"):
        validate_readonly_sparql("SELEC nonsense")


def test_reads_are_scoped_to_the_current_projection_graph() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        assert form["default-graph-uri"] == ["urn:ecg:graph:context:v7"]
        return httpx.Response(200, json={"head": {"vars": []}, "boolean": True})

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        store = FusekiGraphStore(
            "http://fuseki:3030/enterprise",
            client=client,
            graph_uri_provider=lambda: "urn:ecg:graph:context:v7",
        )
        result = store.run_readonly_sparql("ASK { ?s ?p ?o }")

    assert result.boolean is True
    assert result.graph_uri == "urn:ecg:graph:context:v7"


def test_reads_fail_closed_when_no_projection_is_published() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        store = FusekiGraphStore(
            "http://fuseki:3030/enterprise", client=client, graph_uri_provider=lambda: None
        )
        with pytest.raises(GraphQueryError, match="No graph projection"):
            store.run_readonly_sparql("ASK { ?s ?p ?o }")


def test_named_graph_publication_uses_ntriples_and_admin_auth() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.params["graph"] == "urn:ecg:graph:context:v2"
        assert request.headers["content-type"] == "application/n-triples"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(201)

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        FusekiGraphStore(
            "http://fuseki:3030/enterprise",
            admin_user="admin",
            admin_password="local-password",
            client=client,
        ).put_named_graph("urn:ecg:graph:context:v2", b"<urn:a> <urn:b> <urn:c> .\n")
