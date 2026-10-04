from datetime import date

import pytest

from enterprise_context.graph.query import validate_readonly_sparql
from enterprise_context.graph.templates import (
    TEMPLATES,
    GraphTemplateError,
    GraphTemplateService,
    uri_for_identifier,
)
from enterprise_context.security.principals import PrincipalContext


def principal(roles: list[str], units: list[str] | None = None) -> PrincipalContext:
    return PrincipalContext(
        principal_id="p",
        display_name="P",
        buyer_id=None,
        roles=roles,
        business_unit_ids=units or ["BU-000"],
        approval_limit_minor=0,
    )


SAMPLE_PARAMETERS = {
    "suppliers_for_category_with_active_contracts": {"category": "Cloud Services"},
    "products_via_active_contracts": {"entity_id": "supplier-abc"},
    "supplier_relationships": {"entity_id": "supplier-abc"},
    "entity_provenance": {"entity_id": "supplier-abc"},
    "entity_types": {"entity_id": "supplier-abc"},
    "is_business_partner": {"entity_id": "supplier-abc"},
    "neighborhood": {"entity_id": "PR-1007"},
    "requisition_facts": {"entity_id": "PR-1007"},
}


@pytest.mark.parametrize("name", sorted(TEMPLATES))
@pytest.mark.parametrize("roles", [["BUYER"], ["ADMIN"]])
def test_every_template_renders_a_valid_bounded_readonly_query(
    name: str, roles: list[str]
) -> None:
    query = GraphTemplateService.render(name, SAMPLE_PARAMETERS[name], principal(roles))
    query_type, limit = validate_readonly_sparql(query)

    assert query_type in {"SelectQuery", "AskQuery"}
    assert limit <= 100


def test_scoped_principals_get_business_unit_filters_and_global_readers_do_not() -> None:
    buyer_query = GraphTemplateService.render(
        "supplier_relationships", {"entity_id": "supplier-abc"}, principal(["BUYER"])
    )
    admin_query = GraphTemplateService.render(
        "supplier_relationships", {"entity_id": "supplier-abc"}, principal(["ADMIN"])
    )

    assert "business-unit/BU-000" in buyer_query
    assert "scopeBu_" not in admin_query


def test_parameters_cannot_inject_sparql() -> None:
    hostile = 'Cloud") } DELETE WHERE { ?s ?p ?o } #'
    query = GraphTemplateService.render(
        "suppliers_for_category_with_active_contracts",
        {"category": hostile, "as_of": date(2026, 1, 1)},
        principal(["BUYER"]),
    )

    assert validate_readonly_sparql(query)[0] == "SelectQuery"
    assert '\\"' in query
    with pytest.raises(GraphTemplateError):
        GraphTemplateService.render(
            "entity_types", {"entity_id": "supplier> } DELETE"}, principal(["BUYER"])
        )


def test_unknown_templates_and_identifiers_are_rejected() -> None:
    with pytest.raises(GraphTemplateError):
        GraphTemplateService.render("drop_everything", {}, principal(["ADMIN"]))
    with pytest.raises(GraphTemplateError):
        uri_for_identifier("unknown-123")
    assert uri_for_identifier("PR-1007").endswith("/requisition/PR-1007")
