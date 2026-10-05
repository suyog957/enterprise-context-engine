# ADR-007: The context graph is separate from raw source data

**Status:** Accepted

## Context
Source systems disagree: Acme appears as "Acme Corp" (ERP), "ACME Corporation" (supplier master), "ACME CORP" (accounts payable), "Acme Corpp" (contracts) and "ACME" (card transactions).

## Decision
Keep three layers:
1. **Immutable source records** with their source system and IDs, retained even when invalid (quarantine, data-quality issues).
2. **Canonical entities** produced by staged entity resolution: normalization, exact identifiers, blocking, RapidFuzz scoring and confidence bands, with every match decision stored. Review candidates are never auto-merged.
3. **The context graph**, a projection over canonical entities. Every canonical node links to the source records it was derived from (`prov:wasDerivedFrom`) with the match method and confidence.

## Consequences
- "Which Acme aliases were merged?" is answerable with sources and evidence; "ACME" stays a review candidate (87% confidence) rather than a silent merge.
- Resolution can be re-run or reversed without losing source values.
- The graph stays clean and queryable while remaining fully traceable to the systems of origin.
