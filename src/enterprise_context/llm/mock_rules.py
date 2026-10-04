"""Deterministic responses for the mock LLM.

They stand in for a real model in tests and the offline demo, so the generated-SPARQL
and intent-fallback paths are exercised (including an adversarial mutation attempt
that the read-only guard must reject) without network access. Rules are evaluated in
order, so specific questions must precede generic ones.
"""

from __future__ import annotations

import json

_ECG = "PREFIX ecg: <https://example.org/enterprise-context#> "


def _sparql(query: str) -> str:
    return json.dumps({"sparql": _ECG + query})


DEFAULT_MOCK_RULES: tuple[tuple[str, str], ...] = (
    # NL-to-SPARQL generation (prompt contains "Question: ...").
    (
        r"Question:[^\n]*\b(delete|drop|remove|insert|clear)\b",
        json.dumps({"sparql": "DELETE WHERE { ?s ?p ?o }"}),
    ),
    (
        r"Question:[^\n]*how many[^\n]*suppliers[^\n]*blocked",
        _sparql(
            "SELECT (COUNT(DISTINCT ?supplier) AS ?blockedSuppliers) "
            "WHERE { ?supplier ecg:hasSupplierStatus ecg:BLOCKED } LIMIT 1"
        ),
    ),
    (
        r"Question:[^\n]*how many[^\n]*high[- ]risk suppliers",
        _sparql(
            "SELECT (COUNT(DISTINCT ?supplier) AS ?highRiskSuppliers) "
            'WHERE { ?supplier a ecg:Supplier ; ecg:riskRating "HIGH" } LIMIT 1'
        ),
    ),
    (
        r"Question:[^\n]*how many business partners",
        _sparql(
            "SELECT (COUNT(DISTINCT ?partner) AS ?businessPartners) "
            "WHERE { ?partner a ecg:BusinessPartner } LIMIT 1"
        ),
    ),
    (
        r"Question:[^\n]*suppliers[^\n]*blocked|Question:[^\n]*blocked suppliers",
        _sparql(
            "SELECT ?supplier ?name WHERE { ?supplier ecg:hasSupplierStatus ecg:BLOCKED ; "
            "ecg:displayName ?name } ORDER BY ?name LIMIT 50"
        ),
    ),
    (
        r"Question:[^\n]*high[- ]risk suppliers",
        _sparql(
            'SELECT ?supplier ?name WHERE { ?supplier a ecg:Supplier ; ecg:riskRating "HIGH" ; '
            "ecg:displayName ?name } ORDER BY ?name LIMIT 50"
        ),
    ),
    (
        r"Question:[^\n]*(hallucinat|nonexistent predicate)",
        _sparql("SELECT ?s WHERE { ?s ecg:hasMagicProperty ?o } LIMIT 5"),
    ),
    (
        r"Question:[^\n]*how many suppliers",
        _sparql(
            "SELECT (COUNT(DISTINCT ?supplier) AS ?suppliers) "
            "WHERE { ?supplier a ecg:Supplier } LIMIT 1"
        ),
    ),
)
