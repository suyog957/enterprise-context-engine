from collections.abc import Sequence

from enterprise_context.domain.models import EntityResolutionResult, GoldenEntityPair


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_entity_resolution(
    pairs: Sequence[GoldenEntityPair], results: Sequence[EntityResolutionResult]
) -> dict[str, float | int]:
    """Report auto-merge quality separately from candidate-review coverage."""
    by_source_id = {result.source_record_id: result for result in results}
    auto_true_positive = auto_false_positive = auto_false_negative = 0
    candidate_true_positive = candidate_false_positive = candidate_false_negative = 0
    positive_pairs = negative_pairs = evaluated_pairs = 0
    review_candidates = 0

    for result in results:
        if result.decision.value == "review":
            review_candidates += 1

    for pair in pairs:
        left = by_source_id.get(pair.left_source_record_id)
        right = by_source_id.get(pair.right_source_record_id)
        if left is None or right is None:
            continue
        evaluated_pairs += 1

        same_canonical = left.canonical_entity_id == right.canonical_entity_id
        candidate_link = same_canonical or (
            left.candidate_entity_id == right.canonical_entity_id
            or right.candidate_entity_id == left.canonical_entity_id
        )
        if pair.same_entity:
            positive_pairs += 1
            if same_canonical:
                auto_true_positive += 1
            else:
                auto_false_negative += 1
            if candidate_link:
                candidate_true_positive += 1
            else:
                candidate_false_negative += 1
        else:
            negative_pairs += 1
            if same_canonical:
                auto_false_positive += 1
            if candidate_link:
                candidate_false_positive += 1

    auto_precision = _ratio(auto_true_positive, auto_true_positive + auto_false_positive)
    auto_recall = _ratio(auto_true_positive, auto_true_positive + auto_false_negative)
    candidate_precision = _ratio(
        candidate_true_positive, candidate_true_positive + candidate_false_positive
    )
    candidate_recall = _ratio(
        candidate_true_positive, candidate_true_positive + candidate_false_negative
    )
    return {
        "labeled_pairs": evaluated_pairs,
        "auto_merge_precision": auto_precision,
        "auto_merge_recall": auto_recall,
        "auto_merge_f1": (
            2 * auto_precision * auto_recall / (auto_precision + auto_recall)
            if auto_precision + auto_recall
            else 0.0
        ),
        "false_merge_rate": _ratio(auto_false_positive, negative_pairs),
        "false_split_rate": _ratio(auto_false_negative, positive_pairs),
        "review_candidates": review_candidates,
        "candidate_precision": candidate_precision,
        "candidate_recall": candidate_recall,
    }
