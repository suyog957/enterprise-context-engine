"""Safe SQL catalog: named, parameterized, scoped, time-limited read queries.

Neither the model nor API callers can submit SQL. Every query is a reviewed template
whose parameters are validated by a Pydantic model; business-unit scope is injected
from the authenticated principal, never from the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from enterprise_context.observability.tracing import observed_store_call
from enterprise_context.security.principals import PrincipalContext

MAX_PAGE_SIZE = 100
STATEMENT_TIMEOUT_MS = 3000
GLOBAL_READ_ROLES = {"ADMIN", "AUDITOR"}
SCOPED_READ_ROLES = {"BUYER", "MANAGER"}


class SqlCatalogError(RuntimeError):
    """Raised for unknown queries, invalid parameters or database failures."""


class SqlAuthorizationError(SqlCatalogError):
    """Raised when the principal has no role that may read transactional data."""


class Page(BaseModel):
    limit: int = Field(default=25, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0, le=100_000)


class SupplierPeriodParams(Page):
    canonical_entity_id: str = Field(min_length=1, max_length=64)
    period_start: date
    period_end: date = Field(description="Exclusive upper bound.")
    buyer_id: str | None = Field(default=None, max_length=32)


class SupplierParams(Page):
    canonical_entity_id: str = Field(min_length=1, max_length=64)


class StateParams(Page):
    state: str = Field(pattern=r"^(DRAFT|SUBMITTED|APPROVED|REJECTED|CLOSED|CONVERTED)$")


class NameParams(Page):
    # LIKE wildcards are rejected so caller input cannot widen the match.
    name: str = Field(min_length=2, max_length=80, pattern=r"^[^%_\\]+$")


class NoParams(Page):
    pass


@dataclass(frozen=True)
class CatalogQuery:
    name: str
    description: str
    params: type[Page]
    sql: str
    paginated: bool = True


# All queries receive %(global_scope)s and %(business_unit_ids)s from the principal.
CATALOG: dict[str, CatalogQuery] = {
    query.name: query
    for query in (
        CatalogQuery(
            name="spend_by_supplier",
            description=(
                "Purchase-order spend with a canonical supplier in a period, per currency "
                "(no FX conversion), excluding cancelled orders. Optional buyer filter. "
                "Reports orders with missing amounts instead of treating them as zero."
            ),
            params=SupplierPeriodParams,
            paginated=False,
            sql="""
                SELECT po.currency,
                       count(*) AS purchase_orders,
                       count(po.amount) AS priced_orders,
                       count(*) - count(po.amount) AS missing_amount_orders,
                       COALESCE(sum(po.amount), 0) AS total_amount
                FROM purchase_order po
                JOIN buyer b ON b.buyer_id = po.buyer_id
                WHERE po.canonical_supplier_id = %(canonical_entity_id)s
                  AND po.created_at >= %(period_start)s AND po.created_at < %(period_end)s
                  AND po.status <> 'CANCELLED'
                  AND (%(buyer_id)s::text IS NULL OR po.buyer_id = %(buyer_id)s)
                  AND (%(global_scope)s OR b.business_unit_id = ANY(%(business_unit_ids)s))
                GROUP BY po.currency
                ORDER BY po.currency""",
        ),
        CatalogQuery(
            name="purchases_for_supplier",
            description=(
                "Purchase orders recorded against any source identifier of a canonical "
                "supplier, newest first, with the source alias each order was recorded under."
            ),
            params=SupplierParams,
            sql="""
                SELECT po.purchase_order_id, po.requisition_id,
                       po.created_at::date AS ordered_on, po.amount, po.currency, po.status,
                       b.buyer_id, b.name AS buyer_name, b.business_unit_id,
                       po.source_supplier_id, sa.alias AS recorded_supplier_name,
                       po.supplier_source_system AS recorded_in
                FROM purchase_order po
                JOIN buyer b ON b.buyer_id = po.buyer_id
                LEFT JOIN source_supplier_identifier ssi
                  ON ssi.source_system = po.supplier_source_system
                 AND ssi.source_supplier_id = po.source_supplier_id
                LEFT JOIN supplier_alias sa
                  ON sa.source_system = ssi.source_system
                 AND sa.source_record_id = ssi.source_record_id
                WHERE po.canonical_supplier_id = %(canonical_entity_id)s
                  AND (%(global_scope)s OR b.business_unit_id = ANY(%(business_unit_ids)s))
                ORDER BY po.created_at DESC, po.purchase_order_id
                LIMIT %(limit_plus_one)s OFFSET %(offset)s""",
        ),
        CatalogQuery(
            name="buyers_for_supplier",
            description="Buyers who placed purchase orders with a canonical supplier.",
            params=SupplierParams,
            sql="""
                SELECT b.buyer_id, b.name AS buyer_name, b.business_unit_id, po.currency,
                       count(*) AS purchase_orders, COALESCE(sum(po.amount), 0) AS total_amount
                FROM purchase_order po
                JOIN buyer b ON b.buyer_id = po.buyer_id
                WHERE po.canonical_supplier_id = %(canonical_entity_id)s
                  AND po.status <> 'CANCELLED'
                  AND (%(global_scope)s OR b.business_unit_id = ANY(%(business_unit_ids)s))
                GROUP BY b.buyer_id, b.name, b.business_unit_id, po.currency
                ORDER BY purchase_orders DESC, b.buyer_id, po.currency
                LIMIT %(limit_plus_one)s OFFSET %(offset)s""",
        ),
        CatalogQuery(
            name="requisitions_by_state",
            description="Requisitions in a given process state, newest first.",
            params=StateParams,
            sql="""
                SELECT pr.requisition_id, pr.state, pr.amount, pr.currency,
                       pr.business_unit_id, pr.buyer_id, cs.preferred_name AS supplier_name,
                       pr.created_at::date AS created_on
                FROM purchase_requisition pr
                LEFT JOIN canonical_supplier cs
                  ON cs.canonical_entity_id = pr.canonical_supplier_id
                WHERE pr.state = %(state)s
                  AND (%(global_scope)s OR pr.business_unit_id = ANY(%(business_unit_ids)s))
                ORDER BY pr.created_at DESC, pr.requisition_id
                LIMIT %(limit_plus_one)s OFFSET %(offset)s""",
        ),
        CatalogQuery(
            name="find_buyers",
            description="Buyers whose name contains the given text (case-insensitive).",
            params=NameParams,
            sql="""
                SELECT b.buyer_id, b.name AS buyer_name, b.business_unit_id
                FROM buyer b
                WHERE b.name ILIKE '%%' || %(name)s || '%%'
                  AND (%(global_scope)s OR b.business_unit_id = ANY(%(business_unit_ids)s))
                ORDER BY b.name
                LIMIT %(limit_plus_one)s OFFSET %(offset)s""",
        ),
        CatalogQuery(
            name="data_quality_summary",
            description=(
                "Retained data-quality issues: missing amounts, unresolved suppliers, invalid "
                "currencies, entity-resolution review candidates and low-confidence matches."
            ),
            params=NoParams,
            paginated=False,
            sql="""
                WITH scoped_po AS (
                    SELECT po.* FROM purchase_order po
                    JOIN buyer b ON b.buyer_id = po.buyer_id
                    WHERE %(global_scope)s OR b.business_unit_id = ANY(%(business_unit_ids)s)
                )
                SELECT 'missing_po_amount' AS issue, count(*) AS records
                  FROM scoped_po WHERE amount IS NULL
                UNION ALL
                SELECT 'unresolved_po_supplier', count(*)
                  FROM scoped_po WHERE canonical_supplier_id IS NULL
                UNION ALL
                SELECT 'invalid_po_currency', count(*)
                  FROM scoped_po
                 WHERE currency NOT IN ('USD', 'CAD', 'GBP', 'EUR', 'AUD')
                UNION ALL
                SELECT 'entity_review_candidate', count(*)
                  FROM supplier_alias WHERE review_required
                UNION ALL
                SELECT 'low_confidence_entity_match', count(*)
                  FROM supplier_alias
                 WHERE NOT review_required AND resolution_method <> 'new_entity'
                   AND confidence_score < 0.95
                UNION ALL
                SELECT 'unlinked_supplier_alias', count(*)
                  FROM supplier_alias
                 WHERE canonical_entity_id IS NULL AND candidate_entity_id IS NULL""",
        ),
    )
}


class SqlQueryResult(BaseModel):
    query_name: str
    description: str
    rows: list[dict[str, Any]]
    row_count: int
    next_offset: int | None = None
    scope: str


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


ConnectionFactory = Callable[[], psycopg.Connection[dict[str, Any]]]


class SqlCatalog:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_url(cls, database_url: str) -> SqlCatalog:
        def connect() -> psycopg.Connection[dict[str, Any]]:
            return psycopg.connect(database_url, connect_timeout=3, row_factory=dict_row)

        return cls(connect)

    @staticmethod
    def describe() -> list[dict[str, Any]]:
        return [
            {
                "name": query.name,
                "description": query.description,
                "parameters": query.params.model_json_schema(),
            }
            for query in CATALOG.values()
        ]

    def run(
        self, query_name: str, parameters: dict[str, Any], principal: PrincipalContext
    ) -> SqlQueryResult:
        query = CATALOG.get(query_name)
        if query is None:
            raise SqlCatalogError(f"Unknown catalog query: {query_name}")
        global_scope = bool(GLOBAL_READ_ROLES.intersection(principal.roles))
        if not global_scope and not SCOPED_READ_ROLES.intersection(principal.roles):
            raise SqlAuthorizationError("Principal may not read transactional data")
        try:
            params = query.params.model_validate(parameters)
        except ValueError as error:
            raise SqlCatalogError(f"Invalid parameters for {query_name}: {error}") from error
        bound = {
            **params.model_dump(),
            "limit_plus_one": params.limit + 1,
            "global_scope": global_scope,
            "business_unit_ids": list(principal.business_unit_ids),
        }
        try:
            with observed_store_call("postgres", "catalog_query", **{"ecg.query": query_name}):
                with self._connection_factory() as connection, connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    connection.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                    rows = connection.execute(query.sql, bound).fetchall()
        except psycopg.Error as error:
            raise SqlCatalogError(f"Catalog query {query_name} failed") from error
        next_offset: int | None = None
        if query.paginated and len(rows) > params.limit:
            rows = rows[: params.limit]
            next_offset = params.offset + params.limit
        return SqlQueryResult(
            query_name=query_name,
            description=query.description,
            rows=[{key: _json_value(value) for key, value in row.items()} for row in rows],
            row_count=len(rows),
            next_offset=next_offset,
            scope="global" if global_scope else ",".join(principal.business_unit_ids),
        )
