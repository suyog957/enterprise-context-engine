# Ontology, taxonomy, validation and provenance

Files: `ontology/enterprise.ttl` (RDFS/OWL), `ontology/taxonomy.ttl` (SKOS), `ontology/shapes.ttl` (SHACL). Namespace `ecg: <https://example.org/enterprise-context#>`; resources use stable URIs built from canonical IDs, for example `https://example.org/enterprise-context/requisition/PR-1007`.

## Ontology vs semantic layer vs knowledge graph vs context graph
- **Ontology**: the vocabulary and its meaning: classes (Supplier, PurchaseRequisition, Contract, BusinessAction…), properties (`hasSupplier`, `governedByContract`, `hasState`…) and axioms (Supplier ⊑ BusinessPartner ⊑ Organization ⊑ Entity).
- **Knowledge graph**: instance data described with that vocabulary: suppliers, products, contracts and their links.
- **Context graph**: the knowledge graph plus operational context needed for decisions: process state, data-quality status, provenance and change freshness. Request-specific facts such as permissions and allowed actions are deliberately **not** stored (ADR-006); OPA computes them per request.

## Class hierarchy (excerpt)
```text
Entity
├── Person ── Buyer
├── Organization ── BusinessPartner ── Supplier
├── BusinessDocument ── PurchaseRequisition, PurchaseOrder, Contract, Policy
├── ProcessState ── SupplierStatus
├── BusinessAction, BusinessUnit, Product, ProductCategory, SourceSystem
SourceRecord ⊑ prov:Entity
```

## Inference
`graph/build.py` materializes RDFS subclass entailment with `owlrl` before validation (14,179 inferred triples in the current build). RDFLib does not infer on its own; the entailment step is explicit and tested. Demonstration:
```sparql
ASK { <https://example.org/enterprise-context/supplier/supplier-bb05b4fd1d1c0e41> a ecg:BusinessPartner }   # true
```
Acme is asserted only as `ecg:Supplier`; BusinessPartner, Organization and Entity are inferred (template `entity_types`).

## SKOS taxonomy
Technology → Software ("Applications"), Hardware, Cloud Services ("Cloud"); Professional Services → Consulting, Legal, Accounting; Facilities → Office Supplies ("Office products"), Maintenance. Questions about a broad concept match narrower ones through `skos:broader*`:
```sparql
?concept skos:prefLabel|skos:altLabel ?label . FILTER(LCASE(STR(?label)) = "technology")
?narrower skos:broader* ?concept .
?contract ecg:permitsCategory ?narrower ; ecg:displayStatus "ACTIVE" .
```

## SHACL
Shapes require, for example, that suppliers have a canonical ID, name and status; purchase orders reference a supplier, buyer and currency; amounts are numeric and non-negative; contracts have dates and a supplier; requisitions have a valid process state. Nodes that violate a shape are **quarantined, not silently dropped**: they are excluded from the published graph, written to `shacl_quarantine.jsonl` with their source records, and reported on the data-quality dashboard. The current build quarantines 22 nodes, including purchase orders whose supplier IDs do not resolve.

## Provenance
Each canonical node links to every source assertion it came from:
```turtle
<…/supplier/supplier-bb05…> prov:wasDerivedFrom <…/source-record/CONTRACT_REPOSITORY:CONTRACT_REPOSITORY-SUP-0000> .
<…/source-record/CONTRACT_REPOSITORY:…> ecg:hasSourceSystem "CONTRACT_REPOSITORY" ;
    ecg:hasSourceRecordId "CONTRACT_REPOSITORY-SUP-0000" ; ecg:sourceName "Acme Corpp" ;
    ecg:resolutionMethod "exact_domain" ; ecg:confidenceScore 0.99 ; ecg:observedAt "2025-01-15T12:00:00Z"^^xsd:dateTime .
```
Purchase orders created by the platform get a `CONTEXT_PLATFORM` source assertion when the projector applies them.

## Open-world vs closed-world
RDF/OWL reasoning is open-world: a missing contract is unknown, not false. Closed-world expectations ("every purchase order must have a supplier") are enforced by SHACL at build time and by database constraints and the API at write time.

## SPARQL coverage
The templates in `graph/templates.py` demonstrate SELECT, ASK, OPTIONAL, FILTER (including `IN`/`NOT IN`), UNION, property paths (`skos:broader*`, `ecg:ownedByBuyer/ecg:belongsToBusinessUnit`), aggregation (`COUNT`, `MAX`, `GROUP BY`) and DISTINCT.
