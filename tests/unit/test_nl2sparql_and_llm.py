from pathlib import Path

import httpx
import pytest

from enterprise_context.graph.nl2sparql import (
    GeneratedSparql,
    NaturalLanguageGraphQuery,
    SparqlGenerationError,
)
from enterprise_context.graph.query import GraphQueryResult
from enterprise_context.llm.mock_rules import DEFAULT_MOCK_RULES
from enterprise_context.llm.providers import (
    ChatMessage,
    LLMError,
    MockLLM,
    OpenAICompatibleLLM,
    complete_structured,
)
from enterprise_context.security.principals import PrincipalContext

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = "https://example.org/enterprise-context/"
FENCED_JSON = '```json\n{"sparql": "ASK { ?s ?p ?o }"}\n```'


class FakeStore:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def run_readonly_sparql(self, query: str) -> GraphQueryResult:
        self.queries.append(query)
        return GraphQueryResult(query_type="SelectQuery", rows=self.rows, graph_uri="urn:g:v1")


def principal(roles: list[str]) -> PrincipalContext:
    return PrincipalContext(
        principal_id="p",
        display_name="P",
        buyer_id=None,
        roles=roles,
        business_unit_ids=["BU-000"],
        approval_limit_minor=0,
    )


def nl_query(rows: list[dict[str, str]]) -> tuple[NaturalLanguageGraphQuery, FakeStore]:
    store = FakeStore(rows)
    service = NaturalLanguageGraphQuery(
        store, MockLLM(DEFAULT_MOCK_RULES), ontology_dir=ROOT / "ontology"  # type: ignore[arg-type]
    )
    return service, store


def test_generated_query_is_validated_and_executed() -> None:
    service, store = nl_query([{"suppliers": "100"}])
    answer = service.answer("How many suppliers do we have?", principal(["BUYER"]))

    assert answer.method == "generated"
    assert answer.rows == [{"suppliers": "100"}]
    assert "ecg:Supplier" in store.queries[0]


def test_generated_mutations_and_hallucinated_terms_are_rejected_before_execution() -> None:
    service, store = nl_query([])
    with pytest.raises(SparqlGenerationError, match="Mutation"):
        service.answer("Please delete all suppliers", principal(["ADMIN"]))
    with pytest.raises(SparqlGenerationError, match="outside the ontology"):
        service.answer("Use a nonexistent predicate", principal(["ADMIN"]))
    assert store.queries == []


def test_transactional_rows_are_withheld_from_scoped_principals() -> None:
    rows = [
        {"supplier": f"{RESOURCE}supplier/s1", "name": "Acme"},
        {"supplier": f"{RESOURCE}requisition/PR-1", "name": "leak"},
    ]
    service, _ = nl_query(rows)

    scoped = service.answer("Which suppliers are blocked?", principal(["BUYER"]))
    global_reader = service.answer("Which suppliers are blocked?", principal(["AUDITOR"]))

    assert scoped.withheld_rows == 1
    assert scoped.rows == [rows[0]]
    assert "GENERATED_QUERY_ROWS_WITHHELD_BY_SCOPE" in scoped.warnings
    assert global_reader.rows == rows


def test_unmatched_questions_fail_instead_of_guessing() -> None:
    service, _ = nl_query([])
    with pytest.raises(SparqlGenerationError):
        service.answer("What is the meaning of procurement?", principal(["ADMIN"]))


def test_openai_compatible_provider_parses_content_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "choices": [{"message": {"content": FENCED_JSON}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleLLM(
            "http://ollama:11434/v1", "qwen2.5:3b", api_key="k", client=client
        )
        output = complete_structured(
            provider, [ChatMessage(role="user", content="q")], GeneratedSparql
        )

    assert output.sparql == "ASK { ?s ?p ?o }"


def test_structured_output_rejects_non_conforming_text() -> None:
    with pytest.raises(LLMError):
        complete_structured(
            MockLLM(default="not json"), [ChatMessage(role="user", content="x")], GeneratedSparql
        )


def test_specific_count_questions_are_not_answered_by_the_generic_rule() -> None:
    service, store = nl_query([{"blockedSuppliers": "17"}])
    service.answer("How many suppliers are blocked?", principal(["BUYER"]))

    assert "ecg:BLOCKED" in store.queries[0]
