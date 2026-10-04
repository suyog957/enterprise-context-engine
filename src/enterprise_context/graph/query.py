from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
from pydantic import BaseModel, Field
from rdflib import Literal
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue


class GraphQueryError(RuntimeError):
    """Raised when a bounded read-only graph query cannot be completed."""


class GraphQueryValidationError(ValueError):
    """Raised when a SPARQL query exceeds the read-only query contract."""


class GraphQueryResult(BaseModel):
    query_type: str
    rows: list[dict[str, str]] = Field(default_factory=list)
    boolean: bool | None = None


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


def validate_readonly_sparql(query: str, *, max_results: int = 100) -> tuple[str, int]:
    try:
        parsed = parseQuery(query)
    except Exception as error:
        raise GraphQueryValidationError("SPARQL syntax is invalid") from error

    query_form = parsed[1]
    if query_form.name not in {"SelectQuery", "AskQuery"}:
        raise GraphQueryValidationError("Only SELECT and ASK queries are permitted")

    ast_nodes = list(_walk_ast(query_form))
    if any(node.name == "ServiceGraphPattern" for node in ast_nodes):
        raise GraphQueryValidationError("SERVICE clauses are not permitted")
    if any(node.name == "DatasetClause" for node in ast_nodes):
        raise GraphQueryValidationError("External dataset clauses are not permitted")

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

    def replace_default_graph(self, turtle: bytes) -> None:
        owns_client = self._client is None
        client = self._client or httpx.Client(
            timeout=httpx.Timeout(
                self._write_timeout_seconds,
                connect=self._timeout_seconds,
            )
        )
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

    def run_readonly_sparql(self, query: str) -> GraphQueryResult:
        query_type, result_limit = validate_readonly_sparql(
            query, max_results=self._max_results
        )
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout_seconds)
        try:
            response = client.post(
                f"{self._base_url}/query",
                params={"timeout": int(self._timeout_seconds * 1000)},
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            )
            response.raise_for_status()
            payload: Any = response.json()
            if query_type == "AskQuery":
                return GraphQueryResult(query_type=query_type, boolean=bool(payload.get("boolean")))
            bindings = payload.get("results", {}).get("bindings", [])
            rows = [
                {
                    str(variable): str(binding[variable]["value"])
                    for variable in binding
                    if variable in binding
                }
                for binding in bindings[:result_limit]
            ]
            return GraphQueryResult(query_type=query_type, rows=rows)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, GraphQueryError):
                raise
            raise GraphQueryError("Fuseki graph query failed") from error
        finally:
            if owns_client:
                client.close()
