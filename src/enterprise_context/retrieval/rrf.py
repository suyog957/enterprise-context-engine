from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RankedDocument:
    document_id: str
    source: Mapping[str, Any]
    rank: int
    score: float


@dataclass(frozen=True)
class FusedDocument:
    document_id: str
    source: Mapping[str, Any]
    score: float
    bm25_rank: int | None
    bm25_score: float | None
    vector_rank: int | None
    vector_score: float | None


def reciprocal_rank_fusion(
    lexical: Sequence[RankedDocument],
    semantic: Sequence[RankedDocument],
    *,
    rank_constant: int = 60,
    limit: int = 10,
) -> list[FusedDocument]:
    if rank_constant < 1 or limit < 1:
        raise ValueError("RRF rank constant and result limit must be positive")

    fused: dict[str, dict[str, Any]] = {}
    for branch_name, branch in (("lexical", lexical), ("semantic", semantic)):
        for hit in branch:
            item = fused.setdefault(
                hit.document_id,
                {
                    "source": hit.source,
                    "score": 0.0,
                    "bm25_rank": None,
                    "bm25_score": None,
                    "vector_rank": None,
                    "vector_score": None,
                },
            )
            item["score"] += 1.0 / (rank_constant + hit.rank)
            if branch_name == "lexical":
                item["bm25_rank"] = hit.rank
                item["bm25_score"] = hit.score
            else:
                item["vector_rank"] = hit.rank
                item["vector_score"] = hit.score

    ranked = sorted(fused.items(), key=lambda item: (-item[1]["score"], item[0]))[:limit]
    return [
        FusedDocument(
            document_id=document_id,
            source=value["source"],
            score=value["score"],
            bm25_rank=value["bm25_rank"],
            bm25_score=value["bm25_score"],
            vector_rank=value["vector_rank"],
            vector_score=value["vector_score"],
        )
        for document_id, value in ranked
    ]
