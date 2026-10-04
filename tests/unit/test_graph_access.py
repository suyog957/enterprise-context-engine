from urllib.parse import parse_qs

import httpx

from enterprise_context.graph.access import FusekiContextReader
from enterprise_context.graph.query import FusekiGraphStore


def test_graph_reader_escapes_identifier_and_returns_only_bounded_facts() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.content.decode())["query"][0]
        assert "PR-1007" in query
        assert "LIMIT 30" in query
        assert "SERVICE <https://attacker.invalid>" not in query
        return httpx.Response(
            200,
            json={
                "head": {"vars": ["predicate", "object"]},
                "results": {
                    "bindings": [
                        {
                            "predicate": {
                                "type": "uri",
                                "value": "https://example.org/enterprise-context#hasState",
                            },
                            "object": {
                                "type": "uri",
                                "value": "https://example.org/enterprise-context#APPROVED",
                            },
                        }
                    ]
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        reader = FusekiContextReader(
            FusekiGraphStore("http://fuseki:3030/enterprise", client=client)
        )
        facts = reader.get_requisition_facts("PR-1007> } SERVICE <https://attacker.invalid>")

    assert len(facts) == 1
    assert facts[0].predicate == "hasState"
    assert facts[0].object_value.endswith("#APPROVED")
