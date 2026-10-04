from __future__ import annotations

from urllib.parse import quote

from enterprise_context.domain.requisitions import GraphFact
from enterprise_context.graph.query import FusekiGraphStore


class FusekiContextReader:
    def __init__(self, store: FusekiGraphStore) -> None:
        self._store = store

    def get_requisition_facts(self, requisition_id: str) -> list[GraphFact]:
        encoded_id = quote(requisition_id, safe="")
        requisition_uri = f"https://example.org/enterprise-context/requisition/{encoded_id}"
        query = f"""SELECT ?predicate ?object WHERE {{
          VALUES ?requisition {{ <{requisition_uri}> }}
          ?requisition ?predicate ?object .
        }} LIMIT 30"""
        result = self._store.run_readonly_sparql(query)
        facts: list[GraphFact] = []
        for row in result.rows:
            predicate = row.get("predicate", "")
            object_value = row.get("object", "")
            if not predicate or not object_value:
                continue
            facts.append(
                GraphFact(
                    predicate=predicate.rsplit("#", 1)[-1].rsplit("/", 1)[-1],
                    object_value=object_value,
                )
            )
        return facts
