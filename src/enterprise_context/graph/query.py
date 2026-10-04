from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
from pydantic import BaseModel, Field
from rdflib import Literal
from rdflib.plugins.sparql.parser import parseQuery, parseUpdate
from rdflib.plugins.sparql.parserutils import CompValue

from enterprise_context.observability.tracing import observed_store_call


class GraphQueryError(RuntimeError):
    """Raised when a bounded read-only graph query cannot be completed."""


class GraphQueryValidationError(ValueError):
    """Raised when a SPARQL query exceeds the read-only query contract."""


class GraphQueryResult(BaseModel):
    query_type: str
    rows: list[dict[str, str]] = Field(default_factory=list)
    boolean: bool | None = None
    graph_uri: str | None = None


GraphUriProvider = Callable[[], str | None]


def _walk_ast(value: Any) -> Iterator[CompValue]:
    if isinstance(value, CompValue):
        yield value
        for nested in value.values():
            yield from _walk_ast(nested)
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _walk_ast(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_ast(nested)


def _is_update(query: str) -> bool:
    try:
        parseUpdate(query)
    except Exception:
        return False
    return True


def validate_readonly_sparql(query: str, *, max_results: int = 100) -> tuple[str, int]:
    """Validate a query against the read-only contract and return (form, result limit).

    Only SELECT/ASK are allowed. SERVICE, FROM/FROM NAMED and GRAPH patterns are
    rejected so a query cannot leave the current projection or reach remote endpoints.
    """
    try:
        parsed = parseQuery(query)
    except Exception as error:
        if _is_update(query):
            raise GraphQueryValidationError("Mutation queries are not permitted") from error
        raise GraphQueryValidationError("SPARQL syntax is invalid") from error

    query_form = parsed[1]
    if query_form.name not in {"SelectQuery", "AskQuery"}:
        raise GraphQueryValidationError("Only SELECT and ASK queries are permitted")

    ast_nodes = list(_walk_ast(query_form))
    if any(node.name == "ServiceGraphPattern" for node in ast_nodes):
        raise GraphQueryValidationError("SERVICE clauses are not permitted")
    if any(node.name == "DatasetClause" for node in ast_nodes):
        raise GraphQueryValidationError("External dataset clauses are not permitted")
    if any(node.name == "GraphGraphPattern" for node in ast_nodes):
        raise GraphQueryValidationError("GRAPH patterns are not permitted")

    result_limit = max_results
    if query_form.name == "SelectQuery":
        limit_offset = query_form["limitoffset"] if "limitoffset" in query_form else None
        limit_value = (
            limit_offset["limit"]
            if isinstance(limit_offset, CompValue) and "limit" in limit_offset
            else None
        )
        if not isinstance(limit_value, Literal):
            raise GraphQueryValidationError("SELECT queries must specify a LIMIT")
        parsed_limit = limit_value.toPython()
        if not isinstance(parsed_limit, int) or parsed_limit < 1:
            raise GraphQueryValidationError("SELECT LIMIT must be a positive integer")
        if parsed_limit > max_results:
            raise GraphQueryValidationError(f"SELECT LIMIT exceeds the maximum of {max_results}")
        result_limit = parsed_limit

    return query_form.name, result_limit


class FusekiGraphStore:
    """Fuseki adapter for the graph service contract.

    Reads are scoped to the currently published projection graph by passing it as the
    SPARQL-protocol ``default-graph-uri``. Writes publish whole versioned named graphs;
    replacing a populated TDB2 default graph in place is avoided because it stalls.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 3.0,
        write_timeout_seconds: float = 120.0,
        max_results: int = 100,
        admin_user: str | None = None,
        admin_password: str | None = None,
        client: httpx.Client | None = None,
        graph_uri_provider: GraphUriProvider | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._write_timeout_seconds = write_timeout_seconds
        self._max_results = max_results
        self._write_auth = (
            httpx.BasicAuth(admin_user, admin_password)
            if admin_user and admin_password
            else httpx.USE_CLIENT_DEFAULT
        )
        self._client = client
        self._graph_uri_provider = graph_uri_provider

    @property
    def max_results(self) -> int:
        return self._max_results

    def _write_client(self) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        timeout = httpx.Timeout(self._write_timeout_seconds, connect=self._timeout_seconds)
        return httpx.Client(timeout=timeout), True

    def _current_graph_uri(self) -> str | None:
        if self._graph_uri_provider is None:
            return None
        graph_uri = self._graph_uri_provider()
        if graph_uri is None:
            raise GraphQueryError("No graph projection has been published")
        return graph_uri

    def replace_default_graph(self, turtle: bytes) -> None:
        """Replace the default graph. Kept for small graphs and tests only."""
        client, owns_client = self._write_client()
        try:
            response = client.put(
                f"{self._base_url}/data",
                params={"default": ""},
                content=turtle,
                headers={"Content-Type": "text/turtle"},
                auth=self._write_auth,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise GraphQueryError("Unable to publish the validated RDF graph") from error
        finally:
            if owns_client:
                client.close()

    def put_named_graph(self, graph_uri: str, ntriples: bytes) -> None:
        client, owns_client = self._write_client()
        try:
            response = client.put(
                f"{self._base_url}/data",
                params={"graph": graph_uri},
                content=ntriples,
                headers={"Content-Type": "application/n-triples"},
                auth=self._write_auth,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise GraphQueryError(f"Unable to publish graph {graph_uri}") from error
        finally:
            if owns_client:
                client.close()

    def drop_named_graph(self, graph_uri: str) -> None:
        client, owns_client = self._write_client()
        try:
            response = client.delete(
                f"{self._base_url}/data", params={"graph": graph_uri}, auth=self._write_auth
            )
            if response.status_code != 404:
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise GraphQueryError(f"Unable to drop graph {graph_uri}") from error
        finally:
            if owns_client:
                client.close()

    def sparql_update(self, update: str) -> None:
        """Apply a trusted, server-generated SPARQL update (projection worker only)."""
        client, owns_client = self._write_client()
        try:
            response = client.post(
                f"{self._base_url}/update", data={"update": update}, auth=self._write_auth
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise GraphQueryError("SPARQL update failed") from error
        finally:
            if owns_client:
                client.close()

    def _post_query(self, query: str, graph_uri: str | None) -> Any:
        params: dict[str, str | int] = {"timeout": int(self._timeout_seconds * 1000)}
        data: dict[str, str] = {"query": query}
        if graph_uri is not None:
            data["default-graph-uri"] = graph_uri
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout_seconds + 1)
        try:
            with observed_store_call("fuseki", "query", **{"ecg.graph_uri": graph_uri}):
                response = client.post(
                    f"{self._base_url}/query",
                    params=params,
                    data=data,
                    headers={"Accept": "application/sparql-results+json"},
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise GraphQueryError("Fuseki graph query failed") from error
        finally:
            if owns_client:
                client.close()

    def count_triples(self, graph_uri: str) -> int:
        payload = self._post_query("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", graph_uri)
        try:
            return int(payload["results"]["bindings"][0]["n"]["value"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise GraphQueryError("Unexpected triple count response") from error

    def list_named_graphs(self, prefix: str) -> list[str]:
        payload = self._post_query(
            "SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 1000", None
        )
        bindings = payload.get("results", {}).get("bindings", [])
        return [
            str(binding["g"]["value"])
            for binding in bindings
            if str(binding.get("g", {}).get("value", "")).startswith(prefix)
        ]

    def run_readonly_sparql(self, query: str) -> GraphQueryResult:
        query_type, result_limit = validate_readonly_sparql(
            query, max_results=self._max_results
        )
        graph_uri = self._current_graph_uri()
        payload = self._post_query(query, graph_uri)
        try:
            if query_type == "AskQuery":
                return GraphQueryResult(
                    query_type=query_type, boolean=bool(payload.get("boolean")), graph_uri=graph_uri
                )
            bindings = payload.get("results", {}).get("bindings", [])
            rows = [
                {str(variable): str(binding[variable]["value"]) for variable in binding}
                for binding in bindings[:result_limit]
            ]
        except (KeyError, TypeError, AttributeError) as error:
            raise GraphQueryError("Unexpected Fuseki query response") from error
        return GraphQueryResult(query_type=query_type, rows=rows, graph_uri=graph_uri)
