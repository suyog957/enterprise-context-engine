# ADR-002: Graph and relational stores instead of a graph-only system

**Status:** Accepted

## Context
Procurement data has two very different shapes: high-volume transactional facts that need constraints, aggregation and atomic state changes (amounts, purchase orders, approvals, idempotency, audit), and connected semantic context that needs traversal, inference and provenance.

## Decision
PostgreSQL is the system of record for transactional facts, canonical entity mappings, approvals, idempotency records, the audit log and the projection outbox. The RDF graph is a **read projection** built from source data and kept current from the outbox. Writes never go to the graph first.

## Consequences
- Spend questions are SQL aggregations with exact decimal arithmetic and business-unit scoping in the query; graph traversal handles relationships such as categories and contracts.
- Transactions get real guarantees: row locks, a unique index preventing two purchase orders per requisition (verified by an 8-way concurrency test), and idempotency keys.
- The graph can be rebuilt at any time: a new versioned named graph is built from source files and every committed outbox event is replayed before an atomic switch, so rebuilds never lose platform writes.
- Cost: two stores to operate, and the projection can lag. The context envelope reports freshness (graph version plus the watermark of applied changes), and stale projections are allowed for explanation but never used to authorize a write.
