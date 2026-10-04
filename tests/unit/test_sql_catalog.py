from typing import Any

import pytest

from enterprise_context.security.principals import PrincipalContext
from enterprise_context.tools.sql_catalog import (
    CATALOG,
    SqlAuthorizationError,
    SqlCatalog,
    SqlCatalogError,
)


def principal(roles: list[str]) -> PrincipalContext:
    return PrincipalContext(
        principal_id="p",
        display_name="P",
        buyer_id=None,
        roles=roles,
        business_unit_ids=["BU-000", "BU-001"],
        approval_limit_minor=0,
    )


class FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, Any]] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def transaction(self) -> "FakeConnection":
        return self

    def execute(self, sql: str, params: Any = None) -> "FakeConnection":
        self.statements.append((sql, params))
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


def catalog_with(rows: list[dict[str, Any]]) -> tuple[SqlCatalog, FakeConnection]:
    connection = FakeConnection(rows)
    return SqlCatalog(lambda: connection), connection  # type: ignore[arg-type,return-value]


def test_scope_comes_from_the_principal_not_the_caller() -> None:
    catalog, connection = catalog_with([])
    catalog.run(
        "purchases_for_supplier",
        {"canonical_entity_id": "supplier-x", "business_unit_ids": ["BU-999"]},
        principal(["BUYER"]),
    )
    bound = connection.statements[-1][1]

    assert bound["global_scope"] is False
    assert bound["business_unit_ids"] == ["BU-000", "BU-001"]
    assert connection.statements[0][0] == "SET TRANSACTION READ ONLY"
    assert "statement_timeout" in connection.statements[1][0]


def test_global_readers_are_unscoped_and_results_paginate() -> None:
    rows = [{"purchase_order_id": f"PO-{index}"} for index in range(3)]
    catalog, connection = catalog_with(rows)
    result = catalog.run(
        "purchases_for_supplier",
        {"canonical_entity_id": "supplier-x", "limit": 2},
        principal(["AUDITOR"]),
    )

    assert connection.statements[-1][1]["global_scope"] is True
    assert result.row_count == 2
    assert result.next_offset == 2


def test_invalid_parameters_unknown_queries_and_roles_are_rejected() -> None:
    catalog, _ = catalog_with([])
    with pytest.raises(SqlCatalogError):
        catalog.run("drop_tables", {}, principal(["ADMIN"]))
    with pytest.raises(SqlCatalogError):
        catalog.run("requisitions_by_state", {"state": "x'; DROP TABLE--"}, principal(["BUYER"]))
    with pytest.raises(SqlAuthorizationError):
        catalog.run("data_quality_summary", {}, principal(["VISITOR"]))


def test_every_catalog_query_is_parameterized_and_scoped() -> None:
    for query in CATALOG.values():
        assert "%(global_scope)s" in query.sql
        assert "%(business_unit_ids)s" in query.sql
        assert ";" not in query.sql
