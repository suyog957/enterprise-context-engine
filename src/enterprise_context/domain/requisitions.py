from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from enterprise_context.config import get_settings
from enterprise_context.security.principals import PrincipalContext


class SupplierContext(BaseModel):
    canonical_entity_id: str | None
    preferred_name: str | None
    status: str
    risk_rating: str
    approved_categories: list[str]


class GraphFact(BaseModel):
    predicate: str
    object_value: str


class RequisitionContext(BaseModel):
    requisition_id: str
    supplier_source_id: str
    canonical_supplier_id: str | None
    buyer_id: str
    buyer_name: str
    business_unit_id: str
    state: str
    amount: Decimal
    currency: str
    product_ids: list[str]
    categories: list[str]
    supplier: SupplierContext
    active_contract_ids: list[str]
    row_version: int
    graph_facts: list[GraphFact] = Field(default_factory=list)
    graph_available: bool = False


def get_authorized_requisition_context(
    requisition_id: str, principal: PrincipalContext
) -> RequisitionContext | None:
    global_reader = bool({"ADMIN", "AUDITOR"}.intersection(principal.roles))
    if not global_reader and not ({"BUYER", "MANAGER"}.intersection(principal.roles)):
        return None

    settings = get_settings()
    with psycopg.connect(
        settings.database_url, connect_timeout=3, row_factory=dict_row
    ) as connection:
        scope_predicate = "" if global_reader else "AND business_unit_id = ANY(%s)"
        parameters: tuple[Any, ...] = (requisition_id,)
        if not global_reader:
            parameters += (principal.business_unit_ids,)
        scope_row = connection.execute(
            f"""SELECT requisition_id, business_unit_id
                FROM purchase_requisition
                WHERE requisition_id = %s {scope_predicate}""",
            parameters,
        ).fetchone()
        if scope_row is None:
            return None

        row: Any = connection.execute(
            """SELECT requisition_id, source_supplier_id, canonical_supplier_id,
                      buyer_id, business_unit_id, state, amount, currency,
                      product_ids, row_version
               FROM purchase_requisition
               WHERE requisition_id = %s""",
            (requisition_id,),
        ).fetchone()
        if row is None:
            return None

        buyer: Any = connection.execute(
            "SELECT name FROM buyer WHERE buyer_id = %s",
            (row["buyer_id"],),
        ).fetchone()
        supplier: Any = None
        contract_ids: list[str] = []
        if row["canonical_supplier_id"] is not None:
            supplier = connection.execute(
                """SELECT canonical_entity_id, preferred_name, status, risk_rating,
                          approved_categories
                   FROM canonical_supplier WHERE canonical_entity_id = %s""",
                (row["canonical_supplier_id"],),
            ).fetchone()
            contract_rows = connection.execute(
                """SELECT contract_id FROM contract
                   WHERE canonical_supplier_id = %s
                     AND status = 'ACTIVE'
                     AND start_date <= %s AND end_date >= %s""",
                (row["canonical_supplier_id"], date.today(), date.today()),
            ).fetchall()
            contract_ids = [str(contract["contract_id"]) for contract in contract_rows]

        product_ids = [str(product_id) for product_id in row["product_ids"]]
        category_rows: list[Any] = []
        if product_ids:
            category_rows = connection.execute(
                "SELECT DISTINCT category FROM product WHERE product_id = ANY(%s)",
                (product_ids,),
            ).fetchall()
        categories = sorted({str(category["category"]) for category in category_rows})

    return RequisitionContext(
        requisition_id=str(row["requisition_id"]),
        supplier_source_id=str(row["source_supplier_id"]),
        canonical_supplier_id=(
            str(row["canonical_supplier_id"]) if row["canonical_supplier_id"] else None
        ),
        buyer_id=str(row["buyer_id"]),
        buyer_name=str(buyer["name"]) if buyer else "Unknown buyer",
        business_unit_id=str(row["business_unit_id"]),
        state=str(row["state"]),
        amount=Decimal(row["amount"]),
        currency=str(row["currency"]),
        product_ids=product_ids,
        categories=categories,
        supplier=SupplierContext(
            canonical_entity_id=(
                str(supplier["canonical_entity_id"]) if supplier else None
            ),
            preferred_name=str(supplier["preferred_name"]) if supplier else None,
            status=str(supplier["status"]) if supplier else "UNKNOWN",
            risk_rating=str(supplier["risk_rating"]) if supplier else "UNKNOWN",
            approved_categories=(list(supplier["approved_categories"]) if supplier else []),
        ),
        active_contract_ids=contract_ids,
        row_version=int(row["row_version"]),
    )
