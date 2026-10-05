# Entity resolution

Suppliers appear in five systems with inconsistent names and identifiers. Resolution is staged, explainable and reversible; original values are never discarded.

## Batch resolution (`entity_resolution/resolver.py`)
1. **Normalize**: casefold, `&`→`and`, strip punctuation, collapse whitespace, map corporate suffixes (corporation→corp, incorporated→inc, limited→ltd, company→co).
2. **Exact identifiers**: tax ID (confidence 1.0), then web domain (0.99). A conflicting identifier vetoes a merge.
3. **Blocking**: candidates come only from shared tax ID, domain, (country, postal code) or (country, distinctive name token). This avoids O(n²) comparison.
4. **Fuzzy scoring**: RapidFuzz `WRatio` on normalized names (scaled by 0.97 so a fuzzy match never outranks an exact identifier).
5. **Decision bands**: ≥ 0.95 auto-merge; 0.80–0.95 manual-review candidate (kept separate, proposed entity recorded); < 0.80 separate entity.
6. **Precedence**: the ERP system of record seeds canonical entities; other sources merge into them.

Every decision is stored with source system, source record ID, alias, method, confidence and timestamp (`entity_resolution.jsonl`, `source_supplier_identifier`, `supplier_alias`). Embedding similarity is deliberately not a first-line merge criterion (see the plan); it can later be added as a candidate generator.

## Query-time resolution (`POST /entities/resolve`)
Uses the same normalization: exact identifier lookup, then trigram candidates over normalized aliases (PostgreSQL `pg_trgm` GIN index, which acts as the blocking step), then RapidFuzz reranking into MATCH (≥ 0.95), PROBABLE (≥ 0.80) and POSSIBLE (≥ 0.60) bands. An unconfirmed alias can never score higher than its own resolution confidence. Results are authorization-scoped in SQL.

| Query | Result |
|---|---|
| `Acme Corpp` | Acme Corp, MATCH 1.00 (normalized alias from CONTRACT_REPOSITORY) |
| `ACME CORP`, `acme corporation` | Acme Corp, MATCH 1.00 |
| `Acme Corrp` (unseen typo) | Acme Corp, PROBABLE 0.947 |
| `Acme` | Acme Corp, PROBABLE 0.90 (the "ACME" alias is a review candidate capped at 0.87) |
| `Regional Supplier Group` | several close candidates, so the agent asks which one |

The agent accepts MATCH, accepts a PROBABLE match only with a clear margin and says so in the answer, and asks for clarification otherwise.

## Metrics (labelled golden pairs, seed 20261003)
| Metric | Value |
|---|---|
| Labelled pairs | 282 |
| Auto-merge precision | 1.000 |
| Auto-merge recall | 0.981 |
| Auto-merge F1 | 0.990 (gate ≥ 0.95) |
| False merge rate | 0.000 |
| False split rate | 0.019 (review candidates such as "ACME", by design) |
| Review-candidate precision / recall | 1.000 / 1.000 |
