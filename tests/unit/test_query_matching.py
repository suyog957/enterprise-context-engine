from enterprise_context.entity_resolution.normalization import normalize_supplier_name
from enterprise_context.entity_resolution.query_matching import (
    AliasRow,
    MatchBand,
    MatchMethod,
    rank_alias_candidates,
)


def alias(entity_id: str, name: str, *, review: bool = False, confidence: float = 1.0) -> AliasRow:
    return AliasRow(
        entity_id=entity_id,
        alias=name,
        normalized_alias=normalize_supplier_name(name),
        source_system="ERP",
        review_required=review,
        alias_confidence=confidence,
    )


ACME_ALIASES = [
    alias("supplier-acme", "Acme Corp"),
    alias("supplier-acme", "ACME Corporation"),
    alias("supplier-acme", "Acme Corpp"),
    alias("supplier-acme", "ACME", review=True, confidence=0.87),
    alias("supplier-northstar", "Northstar Industrial Inc"),
]


def test_known_typo_alias_resolves_exactly_after_normalization() -> None:
    matches = rank_alias_candidates("Acme Corpp", ACME_ALIASES)

    assert matches[0].entity_id == "supplier-acme"
    assert matches[0].band is MatchBand.MATCH
    assert matches[0].method is MatchMethod.NORMALIZED_NAME


def test_unseen_typo_is_a_probable_match_not_an_exact_one() -> None:
    matches = rank_alias_candidates("Acme Corrp", ACME_ALIASES)

    assert matches[0].entity_id == "supplier-acme"
    assert matches[0].band in {MatchBand.MATCH, MatchBand.PROBABLE}
    assert matches[0].method is MatchMethod.FUZZY_NAME


def test_case_and_suffix_variants_normalize_to_the_same_entity() -> None:
    for query in ("ACME CORP", "acme corporation", "Acme Corp."):
        matches = rank_alias_candidates(query, ACME_ALIASES)
        assert matches[0].entity_id == "supplier-acme"
        assert matches[0].band is MatchBand.MATCH


def test_unconfirmed_alias_cannot_exceed_its_resolution_confidence() -> None:
    matches = rank_alias_candidates("ACME", [ACME_ALIASES[3]])

    assert matches[0].score == 0.87
    assert matches[0].review_required is True
    assert matches[0].band is MatchBand.PROBABLE


def test_unrelated_names_are_not_returned() -> None:
    assert rank_alias_candidates("Zephyr Logistics", ACME_ALIASES) == []


def test_exact_identifier_wins_and_entities_are_deduplicated() -> None:
    matches = rank_alias_candidates(
        "SUP-0000", ACME_ALIASES, exact_identifier_entities=["supplier-acme"]
    )

    assert [match.entity_id for match in matches] == ["supplier-acme"]
    assert matches[0].method is MatchMethod.EXACT_IDENTIFIER
