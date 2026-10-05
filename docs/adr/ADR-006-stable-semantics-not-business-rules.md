# ADR-006: The ontology holds stable semantics, not every business rule

**Status:** Accepted

## Context
OWL can express constraints, and it is tempting to encode "a purchase order requires an approved requisition and an active supplier" in the ontology.

## Decision
Separate concerns explicitly:

| Layer | Responsibility | Example |
|---|---|---|
| Ontology (RDFS/OWL, SKOS) | Stable meaning and hierarchy | Supplier ⊑ BusinessPartner; Cloud Services broader Technology |
| SHACL | Structural data quality | A purchase order must reference a supplier; amounts are numeric and non-negative |
| OPA (Rego) | Dynamic business policy | Supplier must be ACTIVE; amount within approval limit or manager approval |
| Backend API | Final enforcement | Fresh policy check, row lock, idempotency key, atomic write and audit |

## Consequences
- OWL's open-world semantics are never mistaken for closed-world validation; SHACL and the API provide the closed-world checks.
- Policy can change weekly without touching the ontology; the ontology changes rarely and is versioned (its hash is recorded in every evaluation run).
- Computed permissions and allowed actions are never written into the durable graph; they are request-time results.
