"""Evaluation runner.

Suites:
  smoke   offline and deterministic (CI): entity resolution, policy (local Rego mirror),
          intent classification on the golden chat cases, SPARQL template validity.
  full    smoke + live OPA policy, hybrid retrieval, SPARQL by execution result and the
          golden chat/trajectory cases against the running stack (with fault injection).
  er | policy | retrieval | sparql | chat   run one part.

Add --compare-embeddings to evaluate several embedding models for retrieval.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATASET_SEED = 20261003
RAW = ROOT / "data" / "raw" / "generated"
GOLDEN = ROOT / "data" / "golden" / "generated"
OUTPUT_DIR = ROOT / "data" / "evaluation" / "generated"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def entity_resolution_metrics() -> dict[str, Any]:
    from enterprise_context.domain.models import GoldenEntityPair, SupplierSourceRecord
    from enterprise_context.entity_resolution.evaluation import evaluate_entity_resolution
    from enterprise_context.entity_resolution.resolver import resolve_supplier_records

    records = [
        SupplierSourceRecord.model_validate(row)
        for row in read_jsonl(RAW / "supplier_source_records.jsonl")
    ]
    pairs = [
        GoldenEntityPair.model_validate(row) for row in read_jsonl(GOLDEN / "entity_pairs.jsonl")
    ]
    return dict(evaluate_entity_resolution(pairs, resolve_supplier_records(records)))


def policy_metrics(backend: str, limit: int) -> dict[str, Any]:
    from enterprise_context.config import get_settings
    from enterprise_context.evaluation.policy_metrics import evaluate_policy_cases
    from enterprise_context.policy.client import OPAClient
    from enterprise_context.policy.local import evaluate_procurement_policy
    from enterprise_context.policy.models import (
        PrincipalPolicyInput,
        ProcurementPolicyInput,
        RequisitionPolicyInput,
        SupplierPolicyInput,
    )

    suppliers = {row["supplier_id"]: row for row in read_jsonl(RAW / "suppliers.jsonl")}
    buyers = {row["buyer_id"]: row for row in read_jsonl(RAW / "buyers.jsonl")}
    requisitions = {
        row["requisition_id"]: row for row in read_jsonl(RAW / "purchase_requisitions.jsonl")
    }
    products = {row["product_id"]: row for row in read_jsonl(RAW / "products.jsonl")}
    cases = read_jsonl(GOLDEN / "workflow_cases.jsonl")[:limit]
    client = OPAClient(get_settings().opa_url) if backend == "opa" else None
    decisions, expected, latencies = [], [], []
    for case in cases:
        requisition = requisitions[case["requisition_id"]]
        supplier = suppliers[requisition["supplier_id"]]
        buyer = buyers[requisition["buyer_id"]]
        policy_input = ProcurementPolicyInput(
            action="CREATE_PURCHASE_ORDER",
            principal=PrincipalPolicyInput(
                principal_id=f"user-{buyer['buyer_id'].lower()}",
                roles=buyer["roles"],
                business_unit_ids=[buyer["business_unit_id"]],
                approval_limit_minor=int(Decimal(buyer["approval_limit"]) * 100),
            ),
            requisition=RequisitionPolicyInput(
                requisition_id=requisition["requisition_id"],
                business_unit_id=requisition["business_unit_id"],
                state=requisition["state"],
                amount_minor=int(Decimal(requisition["amount"]) * 100),
                categories=sorted(
                    {products[pid]["category"] for pid in requisition["product_ids"]}
                ),
            ),
            supplier=SupplierPolicyInput(
                canonical_entity_id=supplier["supplier_id"],
                status=supplier["status"],
                risk_rating=supplier["risk_rating"],
                approved_categories=supplier["approved_categories"],
            ),
        )
        started = time.perf_counter()
        decision = (
            client.evaluate(policy_input) if client else evaluate_procurement_policy(policy_input)
        )
        latencies.append((time.perf_counter() - started) * 1000)
        decisions.append(decision)
        expected.append(bool(case["expected_allowed"]))
    metrics: dict[str, Any] = dict(evaluate_policy_cases(expected, decisions, latencies))
    metrics["backend"] = backend
    metrics["mismatches"] = [
        {"case_id": case["case_id"], "expected": e, "actual": d.allowed, "codes": d.reason_codes}
        for case, e, d in zip(cases, expected, decisions, strict=True)
        if e != d.allowed
    ]
    return metrics


def classification_metrics() -> dict[str, Any]:
    from enterprise_context.config import get_settings
    from enterprise_context.context_engine.classifier import IntentClassifier, taxonomy_labels
    from enterprise_context.evaluation.chat_eval import ChatCase
    from scripts.golden_cases import EVALUATION_AS_OF

    classifier = IntentClassifier(
        taxonomy_labels(get_settings().ontology_dir), today=lambda: EVALUATION_AS_OF
    )
    cases = [ChatCase.model_validate(row) for row in read_jsonl(GOLDEN / "chat_cases.jsonl")]
    labelled = [case for case in cases if case.expected_intent]
    wrong = [
        {
            "case_id": c.case_id,
            "question": c.question,
            "predicted": predicted,
            "expected": c.expected_intent,
        }
        for c in labelled
        if (predicted := classifier.classify(c.question).intent.value) != c.expected_intent
    ]
    return {
        "labelled_cases": len(labelled),
        "intent_accuracy": round(1 - len(wrong) / len(labelled), 4) if labelled else 1.0,
        "errors": wrong,
    }


def template_validity() -> dict[str, Any]:
    from enterprise_context.graph.query import validate_readonly_sparql
    from enterprise_context.graph.templates import TEMPLATES, GraphTemplateService
    from enterprise_context.security.principals import PrincipalContext

    samples = {
        "suppliers_for_category_with_active_contracts": {"category": "Cloud Services"},
        "neighborhood": {"entity_id": "PR-1007"},
        "requisition_facts": {"entity_id": "PR-1007"},
    }
    failures = []
    for roles in (["BUYER"], ["ADMIN"]):
        principal = PrincipalContext(
            principal_id="eval",
            display_name="eval",
            buyer_id=None,
            roles=roles,
            business_unit_ids=["BU-000"],
            approval_limit_minor=0,
        )
        for name in TEMPLATES:
            try:
                query = GraphTemplateService.render(
                    name, samples.get(name, {"entity_id": "supplier-x"}), principal
                )
                validate_readonly_sparql(query)
            except ValueError as error:
                failures.append({"template": name, "roles": roles, "error": str(error)})
    return {"templates": len(TEMPLATES), "invalid": failures}


def retrieval_metrics(compare: list[str]) -> dict[str, Any]:
    from enterprise_context.config import get_settings
    from enterprise_context.evaluation.retrieval_eval import RetrievalQuery, evaluate_provider
    from enterprise_context.retrieval.dependencies import get_embedding_provider
    from enterprise_context.retrieval.embeddings import (
        EmbeddingProvider,
        FeatureHashEmbeddingProvider,
        SentenceTransformerEmbeddingProvider,
    )
    from enterprise_context.retrieval.models import IndexedDocument

    settings = get_settings()
    documents = [IndexedDocument.model_validate(r) for r in read_jsonl(RAW / "documents.jsonl")]
    queries = [
        RetrievalQuery.model_validate(r) for r in read_jsonl(GOLDEN / "retrieval_queries.jsonl")
    ]
    configured = (
        settings.embedding_model
        if settings.embedding_provider != "feature_hash"
        else "feature_hash"
    )
    providers: dict[str, EmbeddingProvider] = {configured: get_embedding_provider()}
    for name in compare:
        if name in providers:
            continue
        try:
            providers[name] = (
                FeatureHashEmbeddingProvider(settings.embedding_dimensions)
                if name == "feature_hash"
                else SentenceTransformerEmbeddingProvider(name, 384)
            )
        except (RuntimeError, ValueError, OSError) as error:
            print(f"skipping embedding provider {name}: {error}", file=sys.stderr)
    results = {
        name: evaluate_provider(settings.opensearch_url, name, embedder, documents, queries)
        for name, embedder in providers.items()
    }
    return {
        "configured_provider": configured,
        "selected": results[configured]["hybrid"],
        "providers": results,
    }


def sparql_metrics() -> dict[str, Any]:
    import psycopg

    from enterprise_context.config import get_settings
    from enterprise_context.evaluation.sparql_eval import SparqlCase, evaluate_sparql
    from enterprise_context.security.principals import load_principal
    from enterprise_context.tools.dependencies import (
        get_graph_template_service,
        get_nl_graph_query,
    )

    principal = load_principal("user-auditor")
    if principal is None:
        raise SystemExit("user-auditor principal is missing; run ingestion first")

    def supplier_id(name: str) -> str | None:
        with psycopg.connect(get_settings().database_url) as connection:
            row = connection.execute(
                "SELECT canonical_entity_id FROM canonical_supplier WHERE preferred_name = %s",
                (name,),
            ).fetchone()
        return str(row[0]) if row else None

    cases = [SparqlCase.model_validate(r) for r in read_jsonl(GOLDEN / "sparql_cases.jsonl")]
    return evaluate_sparql(
        cases, get_graph_template_service(), get_nl_graph_query(), principal, supplier_id
    )


def chat_metrics(limit: int | None) -> dict[str, Any]:
    import psycopg

    from enterprise_context.agents.workflow import AgentRequest
    from enterprise_context.config import get_settings
    from enterprise_context.evaluation.chat_eval import CaseResult, ChatCase, score_case, summarize
    from enterprise_context.evaluation.live import build_workflow
    from enterprise_context.security.principals import load_principal

    cases = [ChatCase.model_validate(r) for r in read_jsonl(GOLDEN / "chat_cases.jsonl")]
    cases = cases[:limit] if limit else cases
    results: list[CaseResult] = []
    with psycopg.connect(get_settings().database_url) as connection:
        for case in cases:
            drift = None
            if case.precondition_state:
                row = connection.execute(
                    "SELECT state FROM purchase_requisition WHERE requisition_id = %s",
                    (case.precondition_state["requisition_id"],),
                ).fetchone()
                approval = connection.execute(
                    """SELECT 1 FROM approval_request WHERE resource_id = %s
                       AND status = 'APPROVED' AND expires_at > now()""",
                    (case.precondition_state["requisition_id"],),
                ).fetchone()
                if row is None or row[0] != case.precondition_state["state"]:
                    drift = f"requisition state drifted to {row[0] if row else 'missing'}"
                elif approval:
                    drift = "an approval granted outside the golden data exists"
            principal = load_principal(case.principal_id)
            if principal is None:
                drift = f"principal {case.principal_id} missing"
            if drift or principal is None:
                results.append(
                    CaseResult(
                        case_id=case.case_id, category=case.category, passed=False, skipped=drift
                    )
                )
                continue
            started = time.perf_counter()
            response = build_workflow(case.fault).invoke(
                AgentRequest(question=case.question), principal
            )
            results.append(score_case(case, response, (time.perf_counter() - started) * 1000))
    return summarize(results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--suite",
        choices=["smoke", "full", "er", "policy", "retrieval", "sparql", "chat"],
        default="smoke",
    )
    parser.add_argument("--smoke", action="store_true", help="Alias for --suite smoke")
    parser.add_argument("--policy-backend", choices=["local", "opa"], default=None)
    parser.add_argument("--chat-limit", type=int, default=None)
    parser.add_argument(
        "--compare-embeddings",
        default="",
        help="Comma-separated providers, e.g. feature_hash,sentence-transformers/all-MiniLM-L6-v2",
    )
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    suite = "smoke" if args.smoke else args.suite

    from enterprise_context.config import get_settings
    from enterprise_context.evaluation.gates import critical_failures, evaluate_gates
    from enterprise_context.evaluation.metadata import run_metadata

    metrics: dict[str, Any] = {}
    if suite in {"smoke", "full", "er"}:
        metrics["entity_resolution"] = entity_resolution_metrics()
    if suite in {"smoke", "full", "policy"}:
        backend = args.policy_backend or ("opa" if suite == "full" else "local")
        metrics["policy"] = policy_metrics(backend, 75)
    if suite in {"smoke", "full"}:
        metrics["classification"] = classification_metrics()
        metrics["sparql_templates"] = template_validity()
    if suite in {"full", "retrieval"}:
        compare = [name.strip() for name in args.compare_embeddings.split(",") if name.strip()]
        metrics["retrieval"] = retrieval_metrics(compare)
    if suite in {"full", "sparql"}:
        metrics["sparql"] = sparql_metrics()
    if suite in {"full", "chat"}:
        metrics["chat"] = chat_metrics(args.chat_limit)

    gates = evaluate_gates(metrics)
    report = {
        "metadata": run_metadata(ROOT, get_settings(), suite=suite, dataset_seed=DATASET_SEED),
        "metrics": metrics,
        "gates": gates,
        "critical_failures": critical_failures(gates),
    }
    output = args.output or OUTPUT_DIR / (
        "latest.json" if suite == "full" else f"{suite}_latest.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    summary = {
        "suite": suite,
        "output": str(output.relative_to(ROOT)) if output.is_relative_to(ROOT) else str(output),
        "gates": [
            f"{'PASS' if g['passed'] else 'FAIL'}{' (critical)' if g['critical'] else ''} "
            f"{g['gate']}: {g['actual']} {g['comparison']} {g['threshold']}"
            for g in gates
        ],
    }
    print(json.dumps(summary, indent=2))
    if args.fail_on_regression and report["critical_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
