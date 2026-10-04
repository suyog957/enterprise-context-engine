# Enterprise Context Graph Agent

A local-first procurement context platform demonstrating entity resolution, semantic graph context, hybrid retrieval, deterministic policy evaluation, and bounded agent workflows. It uses synthetic data only.

> **Implementation status:** local-first vertical slice implemented and verified against the live Compose stack. Synthetic ingestion, supplier resolution, RDF/SHACL, OPA policy evaluation, hybrid search, a bounded LangGraph flow, approval-gated/idempotent PO execution, entity/graph/evaluation API surfaces, and a multi-view React workspace with Cytoscape graph exploration are in place.

## Start locally

Requirements: Docker Compose v2, Python 3.12+, and Node.js 22+ only when running the web development server outside Docker. The full local stack uses open-source services and no paid cloud account. It has been verified with open-source Docker Engine on Ubuntu 24.04 under WSL 2; Docker Desktop is not required.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open the web workspace at <http://127.0.0.1:5173>. The API OpenAPI page is at <http://127.0.0.1:8000/docs>. Jaeger is at <http://127.0.0.1:16686>; Prometheus is at <http://127.0.0.1:9090>.

To build the web application without Docker:

```powershell
Set-Location apps/web
npm install
npm run build
```

## Seed the local procurement data

After starting Compose, install the Python package and generate/resolve/ingest the synthetic records. These commands use the host-published local service ports:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts/generate_synthetic_data.py
python scripts/resolve_entities.py
$env:DATABASE_URL = "postgresql://context_app:local_dev_only_change_me@127.0.0.1:5432/enterprise_context"
$env:FUSEKI_URL = "http://127.0.0.1:3030/enterprise"
$env:FUSEKI_ADMIN_USER = "admin"
$env:FUSEKI_ADMIN_PASSWORD = "local_dev_only_change_me"
$env:OPENSEARCH_URL = "http://127.0.0.1:9200"
$env:OPA_URL = "http://127.0.0.1:8181"
python scripts/ingest_data.py
python scripts/build_graph.py --upload
python scripts/build_search_index.py
```

For deterministic evaluation without a live OPA service:

```powershell
python scripts/run_evaluation.py --smoke --fail-on-regression
```

To evaluate the live OPA service after Compose is running:

```powershell
python scripts/run_evaluation.py --smoke --fail-on-regression --policy-backend opa
```

The seeded local principal `user-alice` is Alice Morgan. `user-buyer-010` is a manager approver, `user-auditor` is read-only, and `user-admin` has global local-demo scope. `PR-1007` demonstrates a policy-eligible request; `PR-1011` is blocked by supplier status; `PR-1012` exceeds Alice's approval limit and demonstrates human approval. Use the local-only principal header for these requests:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/requisitions/PR-1007 -Headers @{ "X-Dev-Principal" = "user-alice" }
Invoke-RestMethod http://127.0.0.1:8000/requisitions/PR-1007/allowed-actions -Headers @{ "X-Dev-Principal" = "user-alice" }
Invoke-RestMethod http://127.0.0.1:8000/chat -Method Post -Headers @{ "X-Dev-Principal" = "user-alice" } -ContentType "application/json" -Body '{"question":"Can PR-1007 become a purchase order?"}'
Invoke-RestMethod http://127.0.0.1:8000/search -Method Post -Headers @{ "X-Dev-Principal" = "user-alice" } -ContentType "application/json" -Body '{"query":"blocked suppliers","document_types":["PROCUREMENT_POLICY"]}'
Invoke-RestMethod http://127.0.0.1:8000/requisitions/PR-1007/simulate-po -Method Post -Headers @{ "X-Dev-Principal" = "user-alice" }
Invoke-RestMethod http://127.0.0.1:8000/entities/resolve -Method Post -Headers @{ "X-Dev-Principal" = "user-alice" } -ContentType "application/json" -Body '{"query":"Acme"}'
Invoke-RestMethod http://127.0.0.1:8000/graph/query -Method Post -Headers @{ "X-Dev-Principal" = "user-alice" } -ContentType "application/json" -Body '{"query":"SELECT ?s WHERE { ?s ?p ?o } LIMIT 5"}'
```

PO creation is dry-run by default. The approval flow for `PR-1012` is:

```powershell
$approval = Invoke-RestMethod http://127.0.0.1:8000/requisitions/PR-1012/approval-requests -Method Post -Headers @{ "X-Dev-Principal" = "user-alice" }
Invoke-RestMethod "http://127.0.0.1:8000/approvals/$($approval.approval_id)/decision" -Method Post -Headers @{ "X-Dev-Principal" = "user-buyer-010" } -ContentType "application/json" -Body '{"approved":true,"note":"Reviewed synthetic purchase."}'
```

To test a real local write, set `DRY_RUN=false` in `.env`, restart the API container, then submit the approved action with a fresh idempotency key:

```powershell
docker compose up -d api
Invoke-RestMethod http://127.0.0.1:8000/requisitions/PR-1012/create-po -Method Post -Headers @{ "X-Dev-Principal" = "user-alice"; "Idempotency-Key" = "pr-1012-demo-001" } -ContentType "application/json" -Body ('{"approval_id":"' + $approval.approval_id + '"}')
```

`X-Dev-Principal` is a local-only identity shortcut, not authentication suitable for deployment. The app defaults to production identity mode and rejects this header unless `ENVIRONMENT=local`. In Compose, all published service ports bind to loopback. OpenSearch security is disabled only for this isolated local profile.

## Local services

| Service | Local address | Purpose |
|---|---|---|
| Web | `http://127.0.0.1:5173` | React workspace |
| API | `http://127.0.0.1:8000` | FastAPI application |
| PostgreSQL | `127.0.0.1:5432` | Transactional and canonical records |
| Fuseki | `http://127.0.0.1:3030` | Local RDF/SPARQL graph service |
| OpenSearch | `http://127.0.0.1:9200` | Lexical and vector retrieval |
| OPA | `http://127.0.0.1:8181` | Policy decision service |
| Jaeger | `http://127.0.0.1:16686` | Local trace UI |
| Prometheus | `http://127.0.0.1:9090` | Local metrics UI |

## Implementation Status

Implemented: deterministic synthetic generator and golden cases; staged RapidFuzz entity resolution with review candidates; PostgreSQL schema, raw provenance, canonical mappings and transactional ingestion; RDF/OWL + SKOS, RDFS inference, SHACL validation/quarantine and Fuseki Graph Store upload; read-only SPARQL guard and `/graph/query`; OPA Rego and typed client plus deterministic smoke evaluator; BM25+k-NN OpenSearch indexing with RRF and authorization filters; local feature-hash and optional sentence-transformer embeddings; LangGraph workflow and trajectory; approval records, self-approval prevention, policy recheck, row locking and idempotent PO writes; typed FastAPI endpoints including entity, evaluation, and trace surfaces; React context, entity, Cytoscape graph, policy, source, evaluation, and trajectory views; Prometheus metrics and CI checks.

Verified locally: all eight Compose services, synthetic ingestion, Fuseki publication and bounded query, OpenSearch indexing and hybrid retrieval, live OPA evaluation, the agent workflow, human approval, real PO creation, audit lookup, and idempotent replay. The checked-in Postgres integration test exercises ingestion and the approval/idempotency write lifecycle when `TEST_DATABASE_URL` is set.

Remaining production hardening: add a production identity provider, distributed OpenTelemetry spans, automated containerized service tests in CI, fuller trajectory/evaluation dashboards, and the optional AWS Terraform deployment.

The default embedding provider uses deterministic feature hashing so local tests and startup require no model download. Install `.[embeddings]` and set `EMBEDDING_PROVIDER=sentence_transformers` to use the open-source sentence-transformer provider; its model download is required on first use.

Compose credentials and disabled OpenSearch security are for isolated local development only. Do not expose these ports to an untrusted network. Change local passwords before sharing a development environment.

## Project plan

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for architecture decisions, domain and ontology foundations, phase exit gates, security requirements, evaluation, and definition of done. The original project brief is preserved in [Enterprise_context_engine.md](Enterprise_context_engine.md).
