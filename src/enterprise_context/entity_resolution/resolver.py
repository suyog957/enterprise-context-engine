from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from typing import NamedTuple

from rapidfuzz import fuzz

from enterprise_context.domain.models import (
    EntityResolutionResult,
    ResolutionDecision,
    ResolutionMethod,
    SupplierSourceRecord,
)
from enterprise_context.entity_resolution.normalization import (
    normalize_identifier,
    normalize_supplier_name,
)

AUTO_MERGE_THRESHOLD = 0.95
REVIEW_THRESHOLD = 0.80
_GENERIC_NAME_TOKENS = {
    "and",
    "company",
    "corp",
    "group",
    "inc",
    "industries",
    "industrial",
    "limited",
    "llc",
    "ltd",
    "regional",
    "services",
    "supplier",
    "the",
}


class _Candidate(NamedTuple):
    entity_id: str
    confidence: float
    method: ResolutionMethod


def _entity_id(record: SupplierSourceRecord, normalized_name: str) -> str:
    identity = (
        normalize_identifier(record.tax_id)
        or normalize_identifier(record.website_domain)
        or f"{record.country_code.casefold()}:{normalized_name}"
    )
    digest = sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"supplier-{digest}"


def _blocking_tokens(normalized_name: str) -> set[str]:
    return {
        token
        for token in normalized_name.split()
        if len(token) >= 4 and not token.isdigit() and token not in _GENERIC_NAME_TOKENS
    }


def _best_candidate(
    record: SupplierSourceRecord,
    normalized_name: str,
    candidate_ids: set[str],
    members_by_entity: dict[str, list[SupplierSourceRecord]],
) -> _Candidate | None:
    best: _Candidate | None = None
    tax_id = normalize_identifier(record.tax_id)
    domain = normalize_identifier(record.website_domain)

    for entity_id in candidate_ids:
        members = members_by_entity[entity_id]
        entity_tax_ids = {
            identifier
            for member in members
            if (identifier := normalize_identifier(member.tax_id)) is not None
        }
        entity_domains = {
            identifier
            for member in members
            if (identifier := normalize_identifier(member.website_domain)) is not None
        }
        if tax_id and entity_tax_ids and tax_id not in entity_tax_ids:
            continue
        if domain and entity_domains and domain not in entity_domains:
            continue

        for member in members:
            if member.country_code.casefold() != record.country_code.casefold():
                continue

            member_tax_id = normalize_identifier(member.tax_id)
            member_domain = normalize_identifier(member.website_domain)
            if tax_id and member_tax_id and tax_id != member_tax_id:
                continue
            if domain and member_domain and domain != member_domain:
                continue

            member_name = normalize_supplier_name(member.supplier_name)
            if tax_id and member_tax_id and tax_id == member_tax_id:
                candidate = _Candidate(entity_id, 1.0, ResolutionMethod.EXACT_TAX_ID)
            elif domain and member_domain and domain == member_domain:
                candidate = _Candidate(entity_id, 0.99, ResolutionMethod.EXACT_DOMAIN)
            elif normalized_name == member_name:
                candidate = _Candidate(entity_id, 0.97, ResolutionMethod.NORMALIZED_NAME)
            else:
                score = fuzz.WRatio(normalized_name, member_name) / 100 * 0.97
                candidate = _Candidate(entity_id, score, ResolutionMethod.FUZZY_NAME)

            if best is None or candidate.confidence > best.confidence:
                best = candidate

    return best


def resolve_supplier_records(
    records: list[SupplierSourceRecord],
) -> list[EntityResolutionResult]:
    """Resolve records in bounded country/name/identifier blocks, preserving every input."""
    entity_members: dict[str, list[SupplierSourceRecord]] = defaultdict(list)
    tax_index: dict[str, set[str]] = defaultdict(set)
    domain_index: dict[str, set[str]] = defaultdict(set)
    postal_index: dict[tuple[str, str], set[str]] = defaultdict(set)
    name_index: dict[tuple[str, str], set[str]] = defaultdict(set)
    results: list[EntityResolutionResult] = []

    ordered_records = sorted(
        records, key=lambda record: (record.source_system, record.source_record_id)
    )
    for record in ordered_records:
        normalized_name = normalize_supplier_name(record.supplier_name)
        country = record.country_code.casefold()
        tax_id = normalize_identifier(record.tax_id)
        domain = normalize_identifier(record.website_domain)
        candidate_ids: set[str] = set()
        if tax_id:
            candidate_ids.update(tax_index[tax_id])
        if domain:
            candidate_ids.update(domain_index[domain])
        postal_code = normalize_identifier(record.postal_code)
        if postal_code:
            candidate_ids.update(postal_index[(country, postal_code)])
        for token in _blocking_tokens(normalized_name):
            candidate_ids.update(name_index[(country, token)])

        candidate = _best_candidate(record, normalized_name, candidate_ids, entity_members)
        if candidate and candidate.confidence >= AUTO_MERGE_THRESHOLD:
            entity_id = candidate.entity_id
            decision = ResolutionDecision.AUTO_MERGE
        elif candidate and candidate.confidence >= REVIEW_THRESHOLD:
            entity_id = _entity_id(record, normalized_name)
            decision = ResolutionDecision.REVIEW
        else:
            candidate = None
            entity_id = _entity_id(record, normalized_name)
            decision = ResolutionDecision.SEPARATE

        entity_members[entity_id].append(record)
        if tax_id:
            tax_index[tax_id].add(entity_id)
        if domain:
            domain_index[domain].add(entity_id)
        if postal_code:
            postal_index[(country, postal_code)].add(entity_id)
        for token in _blocking_tokens(normalized_name):
            name_index[(country, token)].add(entity_id)

        results.append(
            EntityResolutionResult(
                canonical_entity_id=entity_id,
                source_system=record.source_system,
                source_record_id=record.source_record_id,
                alias=record.supplier_name,
                normalized_name=normalized_name,
                resolution_method=candidate.method if candidate else ResolutionMethod.NEW_ENTITY,
                confidence_score=candidate.confidence if candidate else 0.0,
                decision=decision,
                candidate_entity_id=(
                    candidate.entity_id
                    if decision is ResolutionDecision.REVIEW and candidate
                    else None
                ),
                decided_at=datetime.now(timezone.utc),
            )
        )

    return results
