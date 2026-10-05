# ADR-003: Hybrid retrieval (BM25 + vectors + RRF) instead of vector-only

**Status:** Accepted

## Context
Policy and contract text contains exact identifiers (contract numbers, supplier names, rule IDs) and also paraphrased intents ("vendor on hold cannot get new orders").

## Decision
Index documents in OpenSearch with both BM25 and a k-NN vector field. Run both rankers with the same authorization and metadata filters, fuse with Reciprocal Rank Fusion (k = 60) and return each hit's BM25 rank, vector rank and fused rank as an explanation.

## Evidence (30 golden queries, `docs/evaluation.md`)
| Embeddings | Ranker | Recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|
| feature hashing (offline default) | lexical | 0.967 | 0.933 | 0.942 |
| feature hashing | vector | 0.867 | 0.678 | 0.725 |
| feature hashing | **hybrid** | **1.000** | 0.834 | 0.874 |
| all-MiniLM-L6-v2 | **hybrid** | **1.000** | **0.983** | **0.988** |

Neither single ranker reaches full recall with the offline embedding; the fused list does. With a real sentence-transformer, hybrid is best on every metric.

## Consequences
- Lexical search keeps identifier queries exact; vectors recover paraphrases.
- RRF needs no score calibration between rankers.
- Cost: two queries per search (p50 about 40 ms locally) and an index that is about 2.6 times larger with 384-dimensional sentence-transformer vectors.
