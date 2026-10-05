# ADR-001: RDF (with OWL/SKOS/SHACL) instead of only a property graph

**Status:** Accepted

## Context
The platform must unify supplier, contract, requisition and policy context from several systems, explain *why* facts hold, and expose stable shared meaning (for example "a Supplier is a BusinessPartner") to people, rules and an agent.

## Decision
Model the context graph in RDF. Use RDFS/OWL for the stable vocabulary and class hierarchy, SKOS for the product-category taxonomy, SHACL for structural validation and PROV-O for assertion-level provenance. Serve it from Apache Jena Fuseki over SPARQL 1.1.

## Consequences
- Global, stable identifiers (URIs derived from canonical IDs) let independently produced data merge without key mapping tables.
- Standards give us inference (subclass reasoning is materialized at build time with `owlrl`), validation (SHACL quarantine of 22 invalid nodes in the current data) and provenance without inventing formats.
- SKOS gives hierarchical category queries for free (`skos:broader*` finds Cloud Services suppliers when asking about Technology).
- Cost: SPARQL is less familiar than Cypher, and RDF triple counts grow fast (56k triples for this dataset). Property-graph engines can traverse faster for deep path analytics; we do not need that here.
- The graph interface (`FusekiGraphStore`, templates) is narrow, so a property-graph or Neptune adapter can be added later with conformance tests (see ADR-005, `infra/terraform/modules/neptune`).
