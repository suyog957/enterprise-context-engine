from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from enterprise_context.config import get_settings
from enterprise_context.security.principals import PrincipalContext


class EntityResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=10, ge=1, le=25)


class EntityCandidate(BaseModel):
    canonical_entity_id: str
    entity_type: str = "Supplier"
    preferred_name: str
    status: str
    risk_rating: str
    matched_alias: str | None = None
    source_system: str | None = None
    confidence_score: float | None = None


class EntityResolveResponse(BaseModel):
    query: str
    candidates: list[EntityCandidate]


class EntityAlias(BaseModel):
    source_system: str
    source_supplier_id: str
    source_record_id: str
    alias: str
    resolution_method: str
    confidence_score: float
    review_required: bool


class EntityProvenance(BaseModel):
    source_system: str
    source_record_id: str
    source_supplier_id: str
    fact: str


class EntityContextResponse(BaseModel):
    entity: EntityCandidate
    aliases: list[EntityAlias]
    related_requisition_ids: list[str]
    related_purchase_order_ids: list[str]
    active_contract_ids: list[str]
    provenance: list[EntityProvenance]


def _has_global_entity_scope(principal: PrincipalContext) -> bool:
    return bool({"ADMIN", "AUDITOR"}.intersection(principal.roles))


def _visible_supplier_clause(principal: PrincipalContext) -> tuple[str, tuple[Any, ...]]:
    if _has_global_entity_scope(principal):
        return "TRUE", ()
    if not {"BUYER", "MANAGER"}.intersection(principal.roles):
        return "FALSE", ()
    clause = """(
        EXISTS (
            SELECT 1 FROM purchase_requisition visible_pr
            WHERE visible_pr.canonical_supplier_id = cs.canonical_entity_id
              AND visible_pr.business_unit_id = ANY(%s)
        )
        OR EXISTS (
            SELECT 1 FROM purchase_order visible_po
            JOIN buyer visible_buyer ON visible_buyer.buyer_id = visible_po.buyer_id
            WHERE visible_po.canonical_supplier_id = cs.canonical_entity_id
              AND visible_buyer.business_unit_id = ANY(%s)
        )
    )"""
    return clause, (principal.business_unit_ids, principal.business_unit_ids)


def _candidate_from_row(row: dict[str, Any]) -> EntityCandidate:
    confidence = row.get("confidence_score")
    return EntityCandidate(
        canonical_entity_id=str(row["canonical_entity_id"]),
        preferred_name=str(row["preferred_name"]),
        status=str(row["status"]),
        risk_rating=str(row["risk_rating"]),
        matched_alias=(str(row["matched_alias"]) if row.get("matched_alias") else None),
        source_system=(str(row["source_system"]) if row.get("source_system") else None),
        confidence_score=float(confidence) if isinstance(confidence, Decimal) else confidence,
    )


def resolve_entities(
    request: EntityResolveRequest, principal: PrincipalContext
) -> EntityResolveResponse:
    visibility_clause, visibility_parameters = _visible_supplier_clause(principal)
    query_text = f"%{request.query.strip()}%"
    sql = f"""SELECT cs.canonical_entity_id, cs.preferred_name, cs.status, cs.risk_rating,
                     sr.payload ->> 'supplier_name' AS matched_alias,
                     ssi.source_system, ssi.confidence_score
              FROM canonical_supplier cs
              LEFT JOIN source_supplier_identifier ssi
                ON ssi.canonical_entity_id = cs.canonical_entity_id
              LEFT JOIN source_record sr
                ON sr.source_system = ssi.source_system
               AND sr.source_record_id = ssi.source_record_id
              WHERE {visibility_clause}
                AND (
                    cs.canonical_entity_id ILIKE %s
                    OR cs.preferred_name ILIKE %s
                    OR ssi.source_supplier_id ILIKE %s
                    OR sr.payload ->> 'supplier_name' ILIKE %s
                )
              ORDER BY COALESCE(ssi.confidence_score, 0) DESC, cs.preferred_name
              LIMIT %s"""
    settings = get_settings()
    candidates: list[EntityCandidate] = []
    seen: set[str] = set()
    with psycopg.connect(
        settings.database_url, connect_timeout=3, row_factory=dict_row
    ) as connection:
        rows = connection.execute(
            sql,
            (
                *visibility_parameters,
                query_text,
                query_text,
                query_text,
                query_text,
                request.limit * 3,
            ),
        ).fetchall()
    for row in rows:
        entity_id = str(row["canonical_entity_id"])
        if entity_id in seen:
            continue
        seen.add(entity_id)
        candidates.append(_candidate_from_row(row))
        if len(candidates) >= request.limit:
            break
    return EntityResolveResponse(query=request.query, candidates=candidates)


def get_entity_context(
    canonical_entity_id: str, principal: PrincipalContext
) -> EntityContextResponse | None:
    visibility_clause, visibility_parameters = _visible_supplier_clause(principal)
    settings = get_settings()
    with psycopg.connect(
        settings.database_url, connect_timeout=3, row_factory=dict_row
    ) as connection:
        row = connection.execute(
            f"""SELECT cs.canonical_entity_id, cs.preferred_name, cs.status, cs.risk_rating,
                       NULL::text AS matched_alias, NULL::text AS source_system,
                       NULL::numeric AS confidence_score
                FROM canonical_supplier cs
                WHERE cs.canonical_entity_id = %s AND {visibility_clause}""",
            (canonical_entity_id, *visibility_parameters),
        ).fetchone()
        if row is None:
            return None
        alias_rows = connection.execute(
            """SELECT ssi.source_system, ssi.source_supplier_id, ssi.source_record_id,
                      COALESCE(sr.payload ->> 'supplier_name', ssi.source_supplier_id) AS alias,
                      ssi.resolution_method, ssi.confidence_score, ssi.review_required
               FROM source_supplier_identifier ssi
               LEFT JOIN source_record sr
                 ON sr.source_system = ssi.source_system
                AND sr.source_record_id = ssi.source_record_id
               WHERE ssi.canonical_entity_id = %s
               ORDER BY ssi.source_system, ssi.source_supplier_id""",
            (canonical_entity_id,),
        ).fetchall()
        requisition_rows = connection.execute(
            """SELECT requisition_id FROM purchase_requisition
               WHERE canonical_supplier_id = %s
                 AND (%s OR business_unit_id = ANY(%s))
               ORDER BY created_at DESC LIMIT 50""",
            (
                canonical_entity_id,
                _has_global_entity_scope(principal),
                principal.business_unit_ids,
            ),
        ).fetchall()
        purchase_order_rows = connection.execute(
            """SELECT po.purchase_order_id FROM purchase_order po
               JOIN buyer b ON b.buyer_id = po.buyer_id
               WHERE po.canonical_supplier_id = %s
                 AND (%s OR b.business_unit_id = ANY(%s))
               ORDER BY po.created_at DESC LIMIT 50""",
            (
                canonical_entity_id,
                _has_global_entity_scope(principal),
                principal.business_unit_ids,
            ),
        ).fetchall()
        contract_rows = connection.execute(
            """SELECT contract_id FROM contract
               WHERE canonical_supplier_id = %s AND status = 'ACTIVE'
               ORDER BY end_date DESC LIMIT 50""",
            (canonical_entity_id,),
        ).fetchall()

    aliases = [
        EntityAlias(
            source_system=str(alias["source_system"]),
            source_supplier_id=str(alias["source_supplier_id"]),
            source_record_id=str(alias["source_record_id"]),
            alias=str(alias["alias"]),
            resolution_method=str(alias["resolution_method"]),
            confidence_score=float(alias["confidence_score"]),
            review_required=bool(alias["review_required"]),
        )
        for alias in alias_rows
    ]
    provenance = [
        EntityProvenance(
            source_system=alias.source_system,
            source_record_id=alias.source_record_id,
            source_supplier_id=alias.source_supplier_id,
            fact=f"Alias '{alias.alias}' resolved by {alias.resolution_method}",
        )
        for alias in aliases
    ]
    return EntityContextResponse(
        entity=_candidate_from_row(row),
        aliases=aliases,
        related_requisition_ids=[str(item["requisition_id"]) for item in requisition_rows],
        related_purchase_order_ids=[
            str(item["purchase_order_id"]) for item in purchase_order_rows
        ],
        active_contract_ids=[str(item["contract_id"]) for item in contract_rows],
        provenance=provenance,
    )
