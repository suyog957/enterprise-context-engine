"""SPARQL evaluation by execution results, not by comparing query strings."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, Field

from enterprise_context.evaluation.ranking import percentile
from enterprise_context.graph.nl2sparql import NaturalLanguageGraphQuery, SparqlGenerationError
from enterprise_context.graph.query import GraphQueryError, GraphQueryResult
from enterprise_context.graph.templates import GraphTemplateService
from enterprise_context.security.principals import PrincipalContext


class SparqlCase(BaseModel):
    case_id: str
    mode: str
    template: str | None = None
    question: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    supplier_name: str | None = None
    result_column: str | None = None
    expected: list[str] | None = None
    expected_contains: list[str] | None = None
    expected_boolean: bool | None = None
    expected_scalar: str | None = None
    expected_rejection: bool = False


def _compare(case: SparqlCase, result: GraphQueryResult) -> str | None:
    """Return a failure description, or None when the execution result matches."""
    if case.expected_boolean is not None:
        return None if result.boolean is case.expected_boolean else f"ASK {result.boolean}"
    if case.expected_scalar is not None:
        values = [next(iter(row.values()), None) for row in result.rows]
        return None if values == [case.expected_scalar] else f"scalar {values}"
    column = case.result_column or ""
    actual = sorted({row[column] for row in result.rows if column in row})
    if case.expected is not None and actual != sorted(case.expected):
        missing = sorted(set(case.expected) - set(actual))[:5]
        extra = sorted(set(actual) - set(case.expected))[:5]
        return f"result set differs (missing {missing}, extra {extra})"
    if case.expected_contains is not None and not set(case.expected_contains) <= set(actual):
        return f"missing {sorted(set(case.expected_contains) - set(actual))}"
    return None


def evaluate_sparql(
    cases: Sequence[SparqlCase],
    templates: GraphTemplateService,
    generator: NaturalLanguageGraphQuery,
    principal: PrincipalContext,
    supplier_id: Callable[[str], str | None],
) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    latencies: list[float] = []
    for case in cases:
        started = time.perf_counter()
        outcome: dict[str, Any] = {"case_id": case.case_id, "mode": case.mode}
        try:
            if case.mode == "template":
                parameters = dict(case.parameters)
                if case.supplier_name:
                    entity_id = supplier_id(case.supplier_name)
                    if entity_id is None:
                        raise LookupError(f"supplier {case.supplier_name} not ingested")
                    parameters["entity_id"] = entity_id
                result: GraphQueryResult = templates.run(case.template or "", parameters, principal)
            else:
                answer = generator.answer(case.question or "", principal)
                result = GraphQueryResult(
                    query_type=answer.query_type or "SelectQuery",
                    rows=answer.rows,
                    boolean=answer.boolean,
                )
            outcome["executed"] = True
            if case.expected_rejection:
                outcome["correct"] = False
                outcome["failure"] = "unsafe query was not rejected"
            else:
                failure = _compare(case, result)
                outcome["correct"] = failure is None
                if failure:
                    outcome["failure"] = failure
        except SparqlGenerationError as error:
            outcome["executed"] = False
            outcome["rejected"] = True
            outcome["correct"] = case.expected_rejection
            if not case.expected_rejection:
                outcome["failure"] = str(error)
        except (GraphQueryError, LookupError, ValueError) as error:
            outcome["executed"] = False
            outcome["correct"] = False
            outcome["failure"] = f"{type(error).__name__}: {error}"
        latencies.append((time.perf_counter() - started) * 1000)
        outcomes.append(outcome)

    executable = [
        o
        for o in outcomes
        if not any(c.case_id == o["case_id"] and c.expected_rejection for c in cases)
    ]
    rejections = [o for o in outcomes if o not in executable]
    return {
        "cases": len(outcomes),
        "execution_success_rate": round(sum(o["executed"] for o in executable) / len(executable), 4)
        if executable
        else 1.0,
        "result_accuracy": round(sum(o["correct"] for o in executable) / len(executable), 4)
        if executable
        else 1.0,
        "unsafe_query_rejection_rate": round(
            sum(o["correct"] for o in rejections) / len(rejections), 4
        )
        if rejections
        else 1.0,
        "latency_p50_ms": round(percentile(latencies, 0.5), 2),
        "latency_p95_ms": round(percentile(latencies, 0.95), 2),
        "failures": [o for o in outcomes if not o["correct"]],
    }
