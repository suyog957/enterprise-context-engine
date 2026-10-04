"""Parameterized SPARQL templates for the high-frequency graph workflows.

Templates are the preferred graph access path for the agent and UI. Parameters are
validated by Pydantic and rendered with rdflib's N3 serialization (literals) or
percent-encoded canonical identifiers (URIs), so caller text is never spliced into
the query. Transactional nodes (requisitions, purchase orders, buyers) are filtered
to the principal's business units inside the query itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field
from rdflib import XSD, Literal

from enterprise_context.graph.query import FusekiGraphStore, GraphQueryResult
from enterprise_context.security.principals import PrincipalContext

ECG = "https://example.org/enterprise-context#"
RESOURCE_BASE = "https://example.org/enterprise-context/"
PREFIXES = """PREFIX ecg: <https://example.org/enterprise-context#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""
GLOBAL_READ_ROLES = {"ADMIN", "AUDITOR"}
_KIND_BY_PREFIX = (
    ("supplier-", "supplier"),
    ("PR-", "requisition"),
    ("PO-", "purchase-order"),
    ("CON-", "contract"),
    ("BUY-", "buyer"),
    ("PROD-", "product"),
    ("BU-", "business-unit"),
)


class GraphTemplateError(ValueError):
    """Raised for unknown templates or invalid template parameters."""


def resource_uri(kind: str, identifier: str) -> str:
    return f"{RESOURCE_BASE}{kind}/{quote(identifier, safe='')}"


def uri_for_identifier(identifier: str) -> str:
    """Map a canonical identifier (supplier-..., PR-..., CON-...) to its stable URI."""
    for prefix, kind in _KIND_BY_PREFIX:
        if identifier.startswith(prefix):
            return resource_uri(kind, identifier)
    raise GraphTemplateError(f"Unrecognized canonical identifier: {identifier}")


def _uri(value: str) -> str:
    return f"<{value}>"


def _literal(value: str) -> str:
    return Literal(value).n3()


def _date(value: date) -> str:
    return Literal(value.isoformat(), datatype=XSD.date).n3()


@dataclass(frozen=True)
class Scope:
    global_read: bool
    business_unit_uris: tuple[str, ...]

    @classmethod
    def for_principal(cls, principal: PrincipalContext) -> Scope:
        return cls(
            global_read=bool(GLOBAL_READ_ROLES.intersection(principal.roles)),
            business_unit_uris=tuple(
                resource_uri("business-unit", unit) for unit in principal.business_unit_ids
            ),
        )

    def node_filter(self, variable: str) -> str:
        """Hide transactional nodes owned by business units outside the scope."""
        if self.global_read:
            return ""
        allowed = ", ".join(_uri(unit) for unit in self.business_unit_uris) or "<urn:none>"
        bu_var = f"?scopeBu_{variable.lstrip('?')}"
        return (
            f"OPTIONAL {{ {variable} ecg:belongsToBusinessUnit"
            f"|(ecg:ownedByBuyer/ecg:belongsToBusinessUnit) {bu_var} }}\n"
            f"  FILTER(!BOUND({bu_var}) || {bu_var} IN ({allowed}))"
        )


class EntityParams(BaseModel):
    entity_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")


class CategoryParams(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    as_of: date = Field(default_factory=date.today)


class SupplierAsOfParams(EntityParams):
    as_of: date = Field(default_factory=date.today)


@dataclass(frozen=True)
class GraphTemplate:
    name: str
    description: str
    params: type[BaseModel]
    build: Callable[[Any, Scope], str]


def _suppliers_for_category(params: CategoryParams, scope: Scope) -> str:
    del scope  # supplier and contract reference data is visible to all procurement roles
    return f"""{PREFIXES}
SELECT ?supplier ?supplierName ?status
       (COUNT(DISTINCT ?contract) AS ?activeContracts) (MAX(?end) AS ?latestContractEnd)
WHERE {{
  ?concept skos:prefLabel|skos:altLabel ?label .
  FILTER(LCASE(STR(?label)) = LCASE({_literal(params.category)}))
  ?narrower skos:broader* ?concept .
  ?contract a ecg:Contract ;
            ecg:hasSupplier ?supplier ;
            ecg:permitsCategory ?narrower ;
            ecg:displayStatus "ACTIVE" ;
            ecg:startDate ?start ;
            ecg:endDate ?end .
  FILTER(?start <= {_date(params.as_of)} && ?end >= {_date(params.as_of)})
  ?supplier ecg:displayName ?supplierName .
  OPTIONAL {{ ?supplier ecg:hasSupplierStatus ?status }}
}}
GROUP BY ?supplier ?supplierName ?status
ORDER BY ?supplierName
LIMIT 50"""


def _products_via_active_contracts(params: SupplierAsOfParams, scope: Scope) -> str:
    del scope
    supplier = _uri(uri_for_identifier(params.entity_id))
    return f"""{PREFIXES}
SELECT DISTINCT ?product ?productName ?categoryLabel ?contract
WHERE {{
  ?contract a ecg:Contract ;
            ecg:hasSupplier {supplier} ;
            ecg:permitsCategory ?category ;
            ecg:displayStatus "ACTIVE" ;
            ecg:startDate ?start ;
            ecg:endDate ?end .
  FILTER(?start <= {_date(params.as_of)} && ?end >= {_date(params.as_of)})
  ?product a ecg:Product ; ecg:hasCategory ?category ; ecg:displayName ?productName .
  ?category skos:prefLabel ?categoryLabel .
}}
ORDER BY ?productName
LIMIT 50"""


def _supplier_relationships(params: EntityParams, scope: Scope) -> str:
    supplier = _uri(uri_for_identifier(params.entity_id))
    return f"""{PREFIXES}
SELECT ?relation (COUNT(DISTINCT ?node) AS ?count)
WHERE {{
  {{
    ?node a ecg:PurchaseRequisition ; ecg:hasSupplier {supplier} .
    BIND("requisitions" AS ?relation)
    {scope.node_filter("?node")}
  }} UNION {{
    ?node a ecg:PurchaseOrder ; ecg:hasSupplier {supplier} .
    BIND("purchase_orders" AS ?relation)
    {scope.node_filter("?node")}
  }} UNION {{
    ?node a ecg:Contract ; ecg:hasSupplier {supplier} .
    BIND("contracts" AS ?relation)
  }} UNION {{
    {supplier} ecg:hasApprovedCategory ?node .
    BIND("approved_categories" AS ?relation)
  }} UNION {{
    ?document ecg:hasSupplier {supplier} ; ecg:ownedByBuyer ?node .
    BIND("buyers" AS ?relation)
    {scope.node_filter("?document")}
  }}
}}
GROUP BY ?relation
ORDER BY ?relation
LIMIT 20"""


def _entity_provenance(params: EntityParams, scope: Scope) -> str:
    entity = _uri(uri_for_identifier(params.entity_id))
    return f"""{PREFIXES}
SELECT DISTINCT ?sourceSystem ?sourceRecordId ?alias ?method ?confidence ?observedAt
WHERE {{
  VALUES ?entity {{ {entity} }}
  {scope.node_filter("?entity")}
  ?entity prov:wasDerivedFrom ?record .
  ?record ecg:hasSourceSystem ?sourceSystem ; ecg:hasSourceRecordId ?sourceRecordId .
  OPTIONAL {{ ?record ecg:sourceName ?alias }}
  OPTIONAL {{ ?record ecg:resolutionMethod ?method }}
  OPTIONAL {{ ?record ecg:confidenceScore ?confidence }}
  OPTIONAL {{ ?record ecg:observedAt ?observedAt }}
}}
ORDER BY ?sourceSystem ?sourceRecordId
LIMIT 50"""


def _entity_types(params: EntityParams, scope: Scope) -> str:
    entity = _uri(uri_for_identifier(params.entity_id))
    return f"""{PREFIXES}
SELECT DISTINCT ?type
WHERE {{
  VALUES ?entity {{ {entity} }}
  {scope.node_filter("?entity")}
  ?entity a ?type .
  FILTER(STRSTARTS(STR(?type), "{ECG}"))
}}
ORDER BY ?type
LIMIT 20"""


def _is_business_partner(params: EntityParams, scope: Scope) -> str:
    del scope
    entity = _uri(uri_for_identifier(params.entity_id))
    return f"{PREFIXES}\nASK {{ {entity} a ecg:BusinessPartner }}"


def _neighborhood(params: EntityParams, scope: Scope) -> str:
    focus = _uri(uri_for_identifier(params.entity_id))
    return f"""{PREFIXES}
SELECT DISTINCT ?direction ?predicate ?neighbor ?neighborLabel ?neighborType
WHERE {{
  VALUES ?focus {{ {focus} }}
  {scope.node_filter("?focus")}
  {{ ?focus ?predicate ?neighbor . BIND("out" AS ?direction) }}
  UNION
  {{ ?neighbor ?predicate ?focus . BIND("in" AS ?direction) }}
  FILTER(?predicate NOT IN (rdf:type, prov:wasDerivedFrom))
  OPTIONAL {{ ?neighbor ecg:displayName|skos:prefLabel ?neighborLabel }}
  OPTIONAL {{
    ?neighbor a ?neighborType .
    FILTER(?neighborType IN (ecg:Supplier, ecg:Buyer, ecg:PurchaseRequisition,
      ecg:PurchaseOrder, ecg:Contract, ecg:Product, ecg:ProductCategory, ecg:BusinessUnit,
      ecg:ProcessState, ecg:SupplierStatus))
  }}
  {scope.node_filter("?neighbor")}
}}
LIMIT 100"""


def _requisition_facts(params: EntityParams, scope: Scope) -> str:
    requisition = _uri(uri_for_identifier(params.entity_id))
    return f"""{PREFIXES}
SELECT DISTINCT ?predicate ?object
WHERE {{
  VALUES ?requisition {{ {requisition} }}
  {scope.node_filter("?requisition")}
  ?requisition ?predicate ?object .
  FILTER(?predicate != prov:wasDerivedFrom)
}}
LIMIT 40"""


TEMPLATES: dict[str, GraphTemplate] = {
    template.name: template
    for template in (
        GraphTemplate(
            "suppliers_for_category_with_active_contracts",
            "Suppliers with contracts that are ACTIVE on a date and permit a category or any "
            "narrower SKOS concept (property path skos:broader*, OPTIONAL, FILTER, COUNT/MAX).",
            CategoryParams,
            _suppliers_for_category,
        ),
        GraphTemplate(
            "products_via_active_contracts",
            "Products whose category is permitted by a supplier's currently active contracts.",
            SupplierAsOfParams,
            _products_via_active_contracts,
        ),
        GraphTemplate(
            "supplier_relationships",
            "Counts of requisitions, purchase orders, contracts, categories and buyers "
            "linked to a supplier (UNION + GROUP BY aggregation), business-unit scoped.",
            EntityParams,
            _supplier_relationships,
        ),
        GraphTemplate(
            "entity_provenance",
            "Source-system assertions an entity was derived from, with match method and "
            "confidence (PROV-O wasDerivedFrom).",
            EntityParams,
            _entity_provenance,
        ),
        GraphTemplate(
            "entity_types",
            "Asserted and RDFS-inferred classes of an entity (e.g. Supplier => BusinessPartner).",
            EntityParams,
            _entity_types,
        ),
        GraphTemplate(
            "is_business_partner",
            "ASK whether an entity is (by inference) an ecg:BusinessPartner.",
            EntityParams,
            _is_business_partner,
        ),
        GraphTemplate(
            "neighborhood",
            "One-hop incoming and outgoing relationships of an entity for graph exploration.",
            EntityParams,
            _neighborhood,
        ),
        GraphTemplate(
            "requisition_facts",
            "Graph facts asserted about a requisition (state, supplier, products, contracts).",
            EntityParams,
            _requisition_facts,
        ),
    )
}


class GraphTemplateResult(GraphQueryResult):
    template: str
    parameters: dict[str, Any]


class GraphTemplateService:
    def __init__(self, store: FusekiGraphStore) -> None:
        self._store = store

    @staticmethod
    def describe() -> list[dict[str, Any]]:
        return [
            {
                "name": template.name,
                "description": template.description,
                "parameters": template.params.model_json_schema(),
            }
            for template in TEMPLATES.values()
        ]

    @staticmethod
    def render(name: str, parameters: dict[str, Any], principal: PrincipalContext) -> str:
        template = TEMPLATES.get(name)
        if template is None:
            raise GraphTemplateError(f"Unknown graph template: {name}")
        try:
            params = template.params.model_validate(parameters)
        except ValueError as error:
            raise GraphTemplateError(f"Invalid parameters for {name}: {error}") from error
        return template.build(params, Scope.for_principal(principal))

    def run(
        self, name: str, parameters: dict[str, Any], principal: PrincipalContext
    ) -> GraphTemplateResult:
        query = self.render(name, parameters, principal)
        result = self._store.run_readonly_sparql(query)
        return GraphTemplateResult(
            **result.model_dump(), template=name, parameters=parameters
        )
