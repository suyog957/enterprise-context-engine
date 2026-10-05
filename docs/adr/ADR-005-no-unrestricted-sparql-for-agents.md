# ADR-005: Agents cannot run unrestricted SPARQL

**Status:** Accepted

## Context
A graph endpoint that accepts arbitrary SPARQL can modify data, call remote services (`SERVICE`), read outside the current projection (`GRAPH`/`FROM`), return unbounded results and ignore business-unit scope. The RDF projection is not partitioned by business unit.

## Decision
- The agent uses **parameterized templates** for known workflows. Parameters are validated and rendered as N3 literals or percent-encoded canonical URIs, and business-unit filters are injected into the query.
- When no template fits, natural-language-to-SPARQL output must pass a read-only guard (SELECT/ASK only; mandatory LIMIT ≤ 50; no SERVICE, FROM or GRAPH; mutations rejected explicitly) and an ontology-vocabulary check, so hallucinated predicates are rejected. Rows referencing transactional records are withheld from scoped principals.
- The raw `/graph/query` endpoint is limited to ADMIN and AUDITOR. It previously allowed any buyer to read every business unit's requisitions; this was fixed.
- Queries always run against the current versioned graph through `default-graph-uri`, with timeouts and result caps.

## Consequences
- Unsafe queries are rejected 100% of the time in the golden set, and template results are 100% correct by execution.
- rdflib's SPARQL parser turned out not to be thread-safe under FastAPI's thread pool (valid queries were intermittently rejected), so all parsing is serialized behind a lock.
- Cost: new question types need a template or rely on the generation path, which yields reduced-confidence answers.
