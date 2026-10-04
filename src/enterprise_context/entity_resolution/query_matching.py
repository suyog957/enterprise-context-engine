"""Query-time entity matching.

Candidates come from PostgreSQL trigram retrieval over normalized aliases (the
blocking step); this module scores them with the same normalization and RapidFuzz
scorers as the batch resolver and assigns explicit confidence bands.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz

from enterprise_context.entity_resolution.normalization import normalize_supplier_name

MATCH_THRESHOLD = 0.95
PROBABLE_THRESHOLD = 0.80
POSSIBLE_THRESHOLD = 0.60


class MatchBand(StrEnum):
    MATCH = "MATCH"
    PROBABLE = "PROBABLE"
    POSSIBLE = "POSSIBLE"


class MatchMethod(StrEnum):
    EXACT_IDENTIFIER = "EXACT_IDENTIFIER"
    NORMALIZED_NAME = "NORMALIZED_NAME"
    FUZZY_NAME = "FUZZY_NAME"


@dataclass(frozen=True)
class AliasRow:
    entity_id: str
    alias: str
    normalized_alias: str
    source_system: str
    review_required: bool
    alias_confidence: float


@dataclass(frozen=True)
class ScoredMatch:
    entity_id: str
    score: float
    band: MatchBand
    method: MatchMethod
    matched_alias: str
    source_system: str
    review_required: bool


def band_for(score: float) -> MatchBand | None:
    if score >= MATCH_THRESHOLD:
        return MatchBand.MATCH
    if score >= PROBABLE_THRESHOLD:
        return MatchBand.PROBABLE
    if score >= POSSIBLE_THRESHOLD:
        return MatchBand.POSSIBLE
    return None


def score_alias(normalized_query: str, row: AliasRow) -> tuple[float, MatchMethod]:
    if normalized_query == row.normalized_alias:
        score, method = 1.0, MatchMethod.NORMALIZED_NAME
    else:
        score = (
            max(
                fuzz.ratio(normalized_query, row.normalized_alias),
                fuzz.token_sort_ratio(normalized_query, row.normalized_alias),
                # Partial matches ("acme" within "acme corp") are useful but less certain.
                fuzz.partial_ratio(normalized_query, row.normalized_alias) * 0.9,
            )
            / 100
        )
        method = MatchMethod.FUZZY_NAME
    if row.review_required:
        # An unconfirmed alias cannot carry more certainty than its own resolution.
        score = min(score, row.alias_confidence)
    return score, method


def rank_alias_candidates(
    query: str,
    rows: Iterable[AliasRow],
    *,
    exact_identifier_entities: Iterable[str] = (),
    limit: int = 10,
) -> list[ScoredMatch]:
    """Score alias rows, keep each entity's best evidence, and order by confidence."""
    best: dict[str, ScoredMatch] = {}
    for entity_id in exact_identifier_entities:
        best[entity_id] = ScoredMatch(
            entity_id=entity_id,
            score=1.0,
            band=MatchBand.MATCH,
            method=MatchMethod.EXACT_IDENTIFIER,
            matched_alias=query.strip(),
            source_system="IDENTIFIER",
            review_required=False,
        )
    normalized_query = normalize_supplier_name(query)
    if normalized_query:
        for row in rows:
            score, method = score_alias(normalized_query, row)
            band = band_for(score)
            if band is None:
                continue
            current = best.get(row.entity_id)
            if current is None or score > current.score:
                best[row.entity_id] = ScoredMatch(
                    entity_id=row.entity_id,
                    score=round(score, 4),
                    band=band,
                    method=method,
                    matched_alias=row.alias,
                    source_system=row.source_system,
                    review_required=row.review_required,
                )
    return sorted(best.values(), key=lambda match: (-match.score, match.entity_id))[:limit]
