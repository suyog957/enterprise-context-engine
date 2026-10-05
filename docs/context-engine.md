# Context Engine

`enterprise_context/context_engine/` decides which stores answer a question, retrieves only authorized context through typed tools and returns a `ContextEnvelope`. `POST /context` exposes it directly; the agent calls the same stages.

## Stages
1. **classify**: deterministic rules (`classifier.py`) produce an intent, routes, requisition IDs, supplier and buyer mentions, a SKOS category label (loaded from the taxonomy) and a relative period ("last year" becomes the previous calendar year, using an injectable clock). An optional LLM fallback may only choose parameter-free intents (policy lookup, generated graph question), and its output must be a known enum value.
2. **resolve_entities**: requisition IDs are taken as exact identifiers; suppliers go through `resolve_entity` (MATCH accepted; PROBABLE accepted only with a margin over the runner-up and reported; close candidates trigger a clarification); buyers go through the `find_buyers` SQL catalog query.
3. **retrieve**: a fixed per-intent plan of tool calls (below).
4. **discover_actions**: for requisition intents, `get_allowed_actions` evaluates the whole action catalog in OPA.

## Routing

| Intent | Example | Routes and tools |
|---|---|---|
| REQUISITION_ELIGIBILITY | Can PR-1007 be converted to a purchase order? | SQL `get_requisition_context` (+ graph facts) → graph `entity_provenance` → documents `search_documents` → OPA `get_allowed_actions` |
| ACTION_REQUEST | Create a PO for PR-1012. | Same as above; the agent then plans within the allowed actions |
| SPEND_AGGREGATION | How much did Alice spend with Acme last year? | `resolve_entity`, `find_buyers`, SQL `spend_by_supplier` (per currency, no FX conversion, missing amounts counted not summed) |
| PURCHASE_HISTORY | Show all purchases involving Acme. | `resolve_entity`, SQL `purchases_for_supplier`, `get_entity_context` (merged aliases) |
| BUYERS_FOR_SUPPLIER | Which buyers purchased from Acme? | `resolve_entity`, SQL `buyers_for_supplier` |
| ENTITY_LOOKUP | Which Acme aliases were merged? | `resolve_entity`, `get_entity_context`, graph `entity_provenance` |
| GRAPH_TRAVERSAL | Which suppliers for Cloud Services have active contracts? | graph template (`suppliers_for_category_with_active_contracts`, `products_via_active_contracts`, `supplier_relationships`, `is_business_partner`) |
| GRAPH_QUESTION | How many suppliers are blocked? | `query_graph_nl` (validated generated SPARQL) |
| POLICY_LOOKUP | What is our policy for high-risk suppliers? | `search_documents` (hybrid, ACL-filtered) |
| UNSUPPORTED | Tell me a joke | clarification listing supported questions |

## The envelope
`entities`, `facts` (each with its source system), `relationships`, `documents` (marked `untrusted`; instruction-like text is flagged and its snippet removed), `policies` (status, reason codes, rule references, policy version), `allowed_actions` (catalog semantics with OPA decisions), `provenance`, `records` (SQL or graph rows), `freshness` (graph version, change watermark, policy version), `confidence`, `warnings`, `clarification` and every `tool_call`.

## Partial-failure semantics
- **Optional source fails** (graph facts or provenance for an eligibility question, document search): a warning such as `GRAPH_CONTEXT_UNAVAILABLE` is added, confidence becomes REDUCED, and the answer says what is missing.
- **Required source fails** (the requisition record, a spend query, a graph-traversal template, search for a policy lookup): confidence becomes LOW and the agent declines rather than guessing.
- **Policy unavailable**: fail closed; no action is presented as available.
- **Out of scope or not found**: a clarification, with no facts returned. Authorization happens inside each tool, so out-of-scope data never enters the envelope.
