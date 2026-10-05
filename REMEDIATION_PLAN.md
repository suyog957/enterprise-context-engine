# Remediation Plan — closing the gaps against the brief and IMPLEMENTATION_PLAN.md

> **Status (2026-10-04): all phases completed**, one commit per phase (`git log --oneline`).
> Gates met: Fuseki publish hang fixed (versioned named graphs), query-time ER, OpenTelemetry
> tracing and JSON logs, typed tools and SQL catalog, Context Engine, bounded LangGraph agent,
> outbox projector and replayable rebuilds, evaluation with 87 trajectory cases and CI gates,
> integration/e2e/browser tests, restructured UI, Grafana profile, validated Terraform, docs and ADRs.
> Defects found and fixed along the way are listed in `docs/evaluation.md` and the commit messages.

**Date:** 2026-10-04
**Baseline:** commit `02a1c4f` (vertical slice: ingestion, ER, RDF/SHACL, OPA, hybrid search, PR-only agent, approval/idempotent writes)
**Rule:** every phase ends with a gate; do not start a dependent phase while its gate fails. Open-source components only; paid/cloud providers stay optional adapters.

---

## 0. Verified root causes (diagnosed before planning)

| # | Problem | Root cause (verified) | Fix direction |
|---|---|---|---|
| B1 | `build_graph.py --upload` hangs | Not data size or format. In-memory Fuseki loads the full graph (54,747 triples) in 1.5 s; TDB2 loads 20k triples into a **new named graph** in ~1 s. Only `PUT ?default` that **replaces an already-populated TDB2 default graph** spins at 100% CPU indefinitely and holds the write lock. | Publish each build to a **versioned named graph** (`urn:ecg:graph:context:v{N}`), record the current version in PostgreSQL, select it at query time with the SPARQL-protocol `default-graph-uri` parameter, then drop the previous version. Verified: publish 1.6–1.9 s, query OK, drop ~1 s. |
| B2 | `graph/build.py` fails outside editable installs | `ROOT = Path(__file__).parents[3]` assumes a source checkout. | `ONTOLOGY_DIR` / `DATA_DIR` settings; copy `ontology/` into the API image. |
| B3 | `/chat` only handles `PR-nnnn` questions | Agent is a fixed linear PR pipeline; no intent routing. | Context Engine + tool registry + rebuilt LangGraph workflow (Phases 3–5). |
| B4 | `/entities/resolve` misses "Acme Corpp" | Query path is `ILIKE '%q%'` substring match; the batch resolver's normalization/RapidFuzz is not used at query time. | Normalize query → `pg_trgm` candidate retrieval on normalized aliases → RapidFuzz rerank → confidence bands. |
| B5 | No usable host Python | Windows has Python 3.10 only; WSL Ubuntu has 3.12 but lacks `python3.12-venv`; repo `.venv` is a Windows 3.10 env. | Containerized `tools` service for scripts/tests (no host Python needed) + documented WSL venv. |

---

## Phase 0 — Runtime and developer environment (B2, B5)

**Build**
- WSL: `sudo apt-get install -y python3.12-venv`; venv at `~/.venvs/ontology` (Linux filesystem, not `/mnt/d`, for speed); `pip install -e ".[dev]"`. Remove the stale Windows `.venv` (it is git-ignored and unusable).
- Add a Compose `tools` service (profile `tools`): same base image as the API plus dev deps, repo bind-mounted at `/work`, Compose-network service URLs. Makefile targets run through it by default (`make pipeline`, `make test`, `make evaluate`), so a new user needs only Docker.
- `Settings`: add `ontology_dir`, `policies_dir`; remove `parents[3]` path math from `graph/build.py` and scripts. API Dockerfile copies `ontology/` and runs as a non-root user.
- Versioned SQL migrations: split schema into `infra/sql/NNN_*.sql`, add a tiny runner that uses the existing `schema_migration` table; ingestion stops applying DDL itself.

**Gate:** `make pipeline` (generate → resolve → ingest → graph → index → evaluate) succeeds from a clean `docker compose down -v` using only Docker; `build_graph` works in the image.

## Phase 1 — Graph publication and entity resolution fixes (B1, B4)

**Graph (B1)**
- New table `projection_state(projection, version, graph_uri, source_watermark, status, published_at)`.
- `FusekiGraphStore.publish_version(ntriples) → graph_uri`: PUT to a new named graph → validate triple count with a query → switch `projection_state` in a DB transaction → DELETE the previous named graph. N-Triples upload (streamable, no prefix ambiguity).
- All reads (`run_readonly_sparql`, templates, context reader) send `default-graph-uri=<current>`; user SPARQL containing `FROM`/`FROM NAMED`/`GRAPH`/`SERVICE` is rejected by the guard so it cannot escape the current projection.
- One-time cleanup: the existing TDB2 default graph (54k stale triples) must be cleared — simplest is recreating the `fuseki_data` volume (**local data loss of the derived graph only; it is rebuilt by the pipeline**).
- Explicit mutation detection in the guard (today a `DELETE` returns "syntax invalid"; it should return `mutation_not_allowed`).

**Entity resolution (B4)**
- Migration: `CREATE EXTENSION pg_trgm`; `supplier_alias(normalized_alias, canonical_entity_id, source_record_id, …)` with a GIN trigram index, populated at ingestion using the same `normalization.py` as the batch resolver.
- Query path: exact ID → exact normalized alias → trigram top-K (blocking) → RapidFuzz `token_sort_ratio`/`WRatio` rerank → bands (≥0.95 match, 0.80–0.95 `REVIEW`, <0.80 dropped). Response carries `method`, `score`, `matched_alias`, `review_required`. Authorization scope clause unchanged.

**Gate:** `build_graph --upload` publishes in < 10 s twice in a row, old version dropped; "Acme Corpp", "ACME", "acme corporation" resolve to the Acme canonical entity; unit + integration tests for both.

## Phase 2 — Observability foundation (do this before new features so they are born instrumented)

- Deps: `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, instrumentations for FastAPI, httpx, psycopg; `python-json-logger`.
- `src/enterprise_context/observability/`: `setup_tracing()` (OTLP → Jaeger `:4317`, service name/env/version resources), `setup_logging()` (JSON, includes `trace_id`/`span_id`/`request_id`, redacts `Authorization`, `Idempotency-Key`, passwords; no prompt/document bodies by default), `metrics.py` (Prometheus counters/histograms).
- Manual spans: agent node, tool call, entity resolution, OpenSearch BM25/kNN, SPARQL, SQL catalog query, OPA, LLM invocation (with token counts).
- Metrics: request latency (existing), `tool_errors_total`, `policy_denials_total{reason}`, `agent_steps`, `agent_retries_total`, `task_success_total`, `llm_tokens_total`, per-store latency histograms.
- `x-request-id` ↔ trace ID correlation; `GET /traces/{id}` returns audit events **plus** span summary fetched server-side from Jaeger's query API (admin/auditor only).

**Gate:** one `/chat` call shows a complete trace in Jaeger (API → agent nodes → OPA/SQL/SPARQL/OpenSearch); logs are JSON with trace IDs; new metrics visible in Prometheus.

## Phase 3 — Typed tools, safe SQL catalog, LLM providers, NL→SPARQL

**`src/enterprise_context/tools/`** — registry of typed tools (Pydantic input/output, server-side authorization, timeout, span, bounded retry):
`resolve_entity`, `search_documents`, `query_graph` (named templates), `query_sql` (catalog only), `get_entity_context`, `get_allowed_actions`, `simulate_create_purchase_order`, `get_policy_explanation`, `create_purchase_order` (**write-protected**: the agent can only *propose*; execution requires the existing approval + idempotency endpoint and a human confirmation).

**Safe SQL catalog** — named parameterized queries, no free SQL: `spend_by_supplier(period)`, `spend_by_buyer_and_supplier(buyer, supplier, period)`, `purchases_for_supplier(canonical_id)`, `buyers_for_supplier`, `requisitions_by_state`. All apply the principal's business-unit scope in SQL, paginated, statement timeout.

**SPARQL templates** — `suppliers_for_category_with_active_contracts`, `supplier_relationships`, `products_via_active_contracts`, `aliases_for_entity`, `requisition_context`, `subclass_inference_demo`; covering SELECT, ASK, OPTIONAL, FILTER, property paths (`skos:broader*`), aggregation.

**LLM provider abstraction** (`src/enterprise_context/llm/`): `mock` (deterministic, default, used by all tests), `openai_compatible` (works with open-source **Ollama**/vLLM/llama.cpp servers), `bedrock` (optional, untested adapter). Model names from env only. Optional Compose profile `llm` running Ollama with a small open model (e.g. `qwen2.5:3b`) — documented as ~3 GB RAM; WSL currently has 7.5 GB total, so it stays opt-in.

**NL→SPARQL** — template-first; when no template fits and an LLM provider is configured: ontology/schema context → generate → existing guard (SELECT/ASK, LIMIT, timeout, no GRAPH/SERVICE) → execute → validate returned entity URIs exist and are authorized → answer. Mock provider maps known question patterns to SPARQL so the path is testable offline.

**Gate:** each tool has unit tests for validation, authorization denial, timeout and error mapping; SQL catalog queries proven BU-scoped; guard tests for every blocked construct.

## Phase 4 — Context Engine (Plan Phase 8)

`src/enterprise_context/context_engine/`
- `ContextEnvelope` (Pydantic): `intent`, `entities`, `relationships`, `facts`, `documents`, `policies`, `allowed_actions`, `provenance`, `freshness` (graph projection version/age, index version), `warnings`, `confidence`.
- Deterministic router (rules + resolved entity types; LLM classifier optional behind the provider interface): `REQUISITION_ELIGIBILITY` (SQL + graph + OPA + docs), `SPEND_AGGREGATION` (SQL), `GRAPH_TRAVERSAL` (SPARQL templates), `POLICY_LOOKUP` (hybrid search), `ENTITY_LOOKUP` (ER + aliases/provenance), `ACTION_REQUEST` (eligibility + proposal), `UNSUPPORTED`/`AMBIGUOUS` (asks for clarification instead of guessing).
- Authorization filtering happens inside each retrieval before data enters the envelope. Partial failure: graph/search outage → envelope marked partial with reduced confidence; never fabricated. Stale projection allowed for explanation, never for writes.

**Gate:** route tests for the five brief questions and the seven demo scenarios, plus graph-down and search-down partial-context tests.

## Phase 5 — Agent rebuild (B3; Plan Phase 9 + 10 integration)

- LangGraph nodes per the brief: `receive_request → classify_intent → resolve_entities → retrieve_context → determine_allowed_actions → create_plan → validate_plan → execute_tools → verify_result → generate_response`, with conditional edges (ambiguity → clarification, approval required → pause), `max_agent_steps`, `max_tool_retries`, per-tool timeouts.
- `AgentState` fields exactly as the brief lists; plans are Pydantic structures that may only reference tools in the allowlist and actions in the discovered valid action set; `validate_plan` rejects anything else before execution.
- Retrieved text is passed in a separate "untrusted evidence" channel; output validation checks every cited fact/document exists in the envelope (groundedness).
- Scenario 7 ("Create a PO for PR-1007"): agent discovers actions → simulates → returns `confirmation_required` with exact arguments; the UI/user confirms → existing approval/idempotent endpoint executes → agent verifies resulting state.
- `/chat` response becomes intent-generic (`intent`, `answer`, `envelope`, `citations`, `proposed_action`, `trajectory`); requisition fields move into the envelope (breaking change for the web client, updated in Phase 9).

**Gate:** trajectory tests: correct tool choice/order, no unnecessary calls, no invalid action attempts, no unauthorized context, injection documents ignored, graceful partial failure; all 7 demo scenarios answer correctly with the mock model.

## Phase 6 — Projection consistency (Plan Phase 5)

- `src/enterprise_context/projection/worker.py` + Compose service `projector`: claims outbox rows with `FOR UPDATE SKIP LOCKED`, applies idempotent handlers (graph: SPARQL `DELETE/INSERT` of the affected resource's triples in the current named graph; search: re-index affected documents), bounded retries with backoff → `FAILED` with `last_error`, advances `projection_state.source_watermark`.
- `scripts/rebuild_projections.py`: full rebuild of graph (new version + atomic switch) and search index (new index + alias swap), replays outbox from a watermark.
- Freshness metadata exposed in the envelope and `/health/ready` detail.

**Gate:** committed PO → graph updated by worker; replaying the same event twice is a no-op; rebuild yields the same triple count/content hash as incremental; concurrent conversion still produces one PO.

## Phase 7 — Evaluation expansion (Plan Phase 13)

- Golden sets under `data/golden/` (versioned, generated deterministically): ER pairs (exists), **retrieval** (query → expected doc IDs), **SPARQL** (question → expected result set), **trajectory/chat** (≥75 cases across normal, ambiguous, typo, missing data, policy, authorization, injection, tool failure, graph failure, invalid SPARQL).
- Metrics: ER P/R/F1/false-merge/false-split; retrieval Recall@5/10, Precision@5, MRR, nDCG@10, p50/p95 latency, index size; SPARQL execution success + result-set accuracy; tool selection/argument accuracy; action validity; policy compliance; unauthorized exposure; groundedness; completion; escalation rate.
- **Embedding comparison** (all open source, run on CPU): `feature_hash` baseline vs `sentence-transformers/all-MiniLM-L6-v2` vs `BAAI/bge-small-en-v1.5`; report quality, latency, index size; documented model choice.
- `run_evaluation.py --suite {smoke,full,retrieval,trajectory,sparql,er}`; single report `latest.json` with metadata (seed, git SHA, ontology hash, policy version, prompt version, model/embedding IDs, timestamp); `/evaluation/latest` serves it.
- Quality gates from the brief (ER F1 ≥ 0.95, Recall@10 ≥ 0.90, policy violations = 0, unauthorized exposure = 0, action validity ≥ 0.98, SPARQL success ≥ 0.95); critical gates fail CI.

**Gate:** full suite runs offline with the mock model; smoke suite < 60 s for CI.

## Phase 8 — Tests and CI

- `tests/integration/`: Fuseki (versioned publish/switch/drop, guard, timeout, result cap, inference), OpenSearch (mapping, hybrid ranks, ACL filter before return), OPA (live bundle decisions, fail-closed when down), projector.
- `tests/e2e/` (marker `e2e`, against the live Compose stack): the brief's five questions + seven scenarios, approval pause/resume, dry-run, idempotent create, injection, dependency outage.
- `tests/evaluation/`: gate assertions over the evaluation report.
- CI jobs: (1) ruff, mypy, unit, OPA tests, eval smoke; (2) integration with service containers; (3) e2e via `docker compose` (on PR to main + nightly); (4) web build + Playwright; (5) security: `pip-audit`, `npm audit`, Trivy image scan; (6) `terraform fmt/validate` + TFLint, never apply.

**Gate:** all jobs green on a clean runner.

## Phase 9 — Frontend restructure (Plan Phase 12)

- Split `App.tsx` (980 lines) into `api/` (typed client), `types/`, `components/`, and pages: **Chat/Agent**, **Requisition Details**, **Entity Explorer** (aliases, match evidence, provenance), **Context Graph Explorer** (Cytoscape; node click → properties, relationships, source systems, provenance, aliases), **Policy Decision Viewer**, **Agent Trace Viewer** (trajectory + Jaeger link), **Evaluation & Data Quality** dashboard (missing amounts, invalid currencies, unresolved suppliers, low-confidence matches, SHACL failures, duplicates, stale records).
- `react-router-dom` for addressable pages; chat shows confirm-to-execute for proposed writes.
- Vitest for components; Playwright smoke for allowed / blocked / approval-required / dry-run flows at desktop and mobile widths.

**Gate:** Playwright suite green; I verify the UI in a real browser.

## Phase 10 — Grafana profile

Compose profile `observability`: Grafana OSS with provisioned Prometheus + Jaeger datasources and a dashboard (API latency, agent steps/success, tool errors, policy denials, per-store latency, LLM tokens).

**Gate:** `docker compose --profile observability up` shows populated dashboards after the demo run.

## Phase 11 — Terraform (optional AWS, never auto-applied)

`infra/terraform/modules/{network, ecs_service, rds_postgres, opensearch, s3_documents, secrets, observability, neptune (optional, disabled by default), bedrock_access}` + `envs/dev`. Least-privilege IAM, private subnets, encryption, log retention, tags. `docs/deployment.md` with cost estimates and the caveat that the Neptune adapter is unvalidated. CI runs `fmt`/`validate`/TFLint only.

**Gate:** `terraform validate` passes for `envs/dev`; no credentials required.

## Phase 12 — Documentation

`docs/architecture.md`, `ontology.md` (RDF/OWL rationale, inference, open- vs closed-world), `entity-resolution.md`, `context-engine.md`, `agent-design.md`, `evaluation.md` (with real numbers), `security.md` (threat model, RBAC, injection defenses), `deployment.md`, and `docs/adr/ADR-001…007` exactly as the brief names them. README refreshed: status, diagrams, demo scenarios, measured resource footprint, limitations.

**Gate:** following the README from a clean clone reaches a working demo with no undocumented steps.

---

## Sequencing

```
P0 env ─► P1 graph+ER fixes ─► P2 observability ─► P3 tools ─► P4 context engine ─► P5 agent
                                        └──────────► P6 projector (parallel with P3–P5)
P5 ─► P7 evaluation ─► P8 tests/CI ─► P9 UI ─► P10 Grafana ─► P11 Terraform ─► P12 docs
```
ADR/doc stubs are written alongside each phase and completed in P12.

## Decisions needing confirmation

1. **Recreate the `fuseki_data` volume once** (derived graph only; rebuilt by the pipeline).
2. **Breaking `/chat` response shape** (intent-generic), with the web client updated in the same phase.
3. **Local LLM via Ollama stays opt-in** (memory); mock model remains the default and the CI model.
4. **Installing `python3.12-venv` in WSL via apt** (needs sudo) in addition to the containerized `tools` path.
