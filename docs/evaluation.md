# Evaluation

`scripts/run_evaluation.py` scores the system on golden data whose expectations come from an **independent oracle** over the synthetic source records (`scripts/golden_cases.py`), never from the system under test. Agents are scored on their whole **trajectory**: intent, the order of tool calls, tool arguments, forbidden tools (invalid action attempts), the policy decision exposed, the proposal produced, leaks of out-of-scope data, grounding, escalation and latency.

```bash
make evaluate-smoke   # offline, deterministic, runs in CI on every push
make evaluate         # full suite against the running stack (CI e2e job, nightly)
docker compose --profile tools run --rm tools python scripts/run_evaluation.py --suite retrieval \
    --compare-embeddings feature_hash,sentence-transformers/all-MiniLM-L6-v2,BAAI/bge-small-en-v1.5
```
Every report records the dataset seed, code revision, ontology hash, policy version and bundle hash, prompt version and LLM and embedding identifiers.

## Golden sets
| Set | Size | Content |
|---|---|---|
| Chat / trajectory cases | 87 | normal, policy violation, action request, authorization, prompt injection, entity resolution, typo, similar names, missing data, graph, policy lookup, tool failure, graph failure, invalid SPARQL, ambiguous, unsupported |
| Retrieval queries | 30 | lexical, paraphrase and contract queries with relevance judgements |
| SPARQL cases | 19 | templates and generated queries compared **by execution result**, plus unsafe queries that must be rejected |
| Entity pairs | 282 | labelled same/different supplier pairs |
| Policy cases | 75 | expected CREATE_PURCHASE_ORDER outcomes |

Fault cases inject real outages (unreachable Fuseki, OpenSearch or OPA) into the live agent to test degradation.

## Results (full suite, local Compose stack, mock LLM)
| Gate | Threshold | Result |
|---|---|---|
| Entity resolution F1 (critical) | ≥ 0.95 | **0.990** |
| Policy violation rate, OPA (critical) | = 0 | **0** (75/75 correct) |
| Chat policy violation rate (critical) | = 0 | **0** |
| Unauthorized data exposure (critical) | = 0 | **0** |
| Action validity (critical) | ≥ 0.98 | **1.0** |
| Retrieval Recall@10 | ≥ 0.90 | **1.0** |
| SPARQL execution success | ≥ 0.95 | **1.0** |
| SPARQL result accuracy | ≥ 0.95 | **1.0** |
| Chat task completion | ≥ 0.90 | **1.0** (86/86; 1 case skipped for state drift) |
| Intent accuracy | ≥ 0.95 | **1.0** |

Other trajectory metrics: tool selection 1.0, tool arguments 1.0, groundedness 1.0, escalation rate 0.22 (clarifications plus approval-required proposals), agent latency p50 95 ms / p95 396 ms. SPARQL p50 59 ms / p95 269 ms. Unsafe generated queries were rejected 100% of the time. LLM cost is 0 with the mock provider; token counters are exported per provider for real models.

**Drift handling.** Cases declare preconditions (requisition state, no externally granted approvals). Cases whose preconditions no longer hold in the live database are reported as skipped, not silently counted as passes.

### Defects found by evaluation
- A requisition that was blocked *and* over the approval limit was reported as APPROVAL_REQUIRED (and could accept an approval request). Fixed in policy 0.2.1, with Rego tests.
- Simulation was not approval-aware, so after a manager approval, discovery, simulation and dry-run execution disagreed. Fixed.
- The mock NL-to-SPARQL rules answered "how many suppliers are blocked" with the total supplier count. Rule ordering was fixed, with a regression test.

## Retrieval and embedding selection
| Embedding model | Index size | Ranker | Recall@5 | Recall@10 | P@5 | MRR | nDCG@10 | p50 |
|---|---|---|---|---|---|---|---|---|
| feature hashing (384-d) | 215 KB | lexical | 0.967 | 0.967 | 0.193 | 0.933 | 0.942 | 44 ms |
| | | vector | 0.800 | 0.867 | 0.160 | 0.678 | 0.725 | 40 ms |
| | | hybrid | 0.933 | 1.000 | 0.187 | 0.834 | 0.874 | 38 ms |
| all-MiniLM-L6-v2 | 562 KB | vector | 0.967 | 1.000 | 0.193 | 0.950 | 0.962 | 91 ms |
| | | **hybrid** | **1.000** | **1.000** | 0.200 | **0.983** | **0.988** | 79 ms |
| bge-small-en-v1.5 | 562 KB | vector | 0.967 | 0.967 | 0.193 | 0.917 | 0.930 | 79 ms |
| | | hybrid | 1.000 | 1.000 | 0.200 | 0.958 | 0.969 | 102 ms |

(Precision@5 is bounded at 0.2 because most queries have exactly one relevant document.)

**Decision.** The default is deterministic feature hashing: zero downloads, offline CI and full hybrid recall. **all-MiniLM-L6-v2 is the recommended production setting** (`EMBEDDING_PROVIDER=sentence_transformers`): it has the best ranking quality (MRR 0.983) at about 2.6 times the index size and roughly 2 times the query latency on CPU. bge-small-en-v1.5 was not better on this corpus. Paraphrase queries are where the models differ: lexical-only recall on paraphrases is 0.83, while every hybrid configuration reaches 1.0.

## Limits of this evaluation
The corpus and golden sets are synthetic and small. With the mock LLM, the evaluation measures the system (routing, retrieval, policy, tools and safety) rather than a model's language quality. Results come from one developer machine.
