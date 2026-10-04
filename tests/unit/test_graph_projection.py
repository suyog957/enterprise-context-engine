import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from rdflib import RDF, Graph, Namespace, URIRef
from scripts.generate_synthetic_data import generate

from enterprise_context.domain.models import SupplierSourceRecord
from enterprise_context.entity_resolution.resolver import resolve_supplier_records
from enterprise_context.graph.build import build_context_graph

ROOT = Path(__file__).resolve().parents[2]
ECG = Namespace("https://example.org/enterprise-context#")
PROV = Namespace("http://www.w3.org/ns/prov#")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def test_graph_builder_infers_provenance_and_quarantines_invalid_records(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    generate(seed=20261003, output_root=data_root)
    raw = data_root / "raw" / "generated"
    source_records = [
        SupplierSourceRecord.model_validate(row)
        for row in read_jsonl(raw / "supplier_source_records.jsonl")
    ]
    resolutions = resolve_supplier_records(source_records)
    canonical = data_root / "canonical" / "generated"
    canonical.mkdir(parents=True, exist_ok=True)
    with (canonical / "entity_resolution.jsonl").open("w", encoding="utf-8") as output:
        for result in resolutions:
            output.write(result.model_dump_json() + "\n")

    summary = build_context_graph(data_root=data_root, ontology_root=ROOT / "ontology")
    graph = Graph().parse(canonical / "context_graph.ttl", format="turtle")
    report = Graph().parse(canonical / "shacl_report.ttl", format="turtle")
    quarantine = read_jsonl(canonical / "shacl_quarantine.jsonl")

    acme_resolution = next(
        result for result in resolutions if result.source_record_id == "ERP-SUP-0000"
    )
    acme = URIRef(
        "https://example.org/enterprise-context/supplier/"
        + quote(acme_resolution.canonical_entity_id, safe="")
    )
    assert (acme, RDF.type, ECG.Supplier) in graph
    assert (acme, RDF.type, ECG.BusinessPartner) in graph
    assert (acme, PROV.wasDerivedFrom, None) in graph
    assert summary["inferred_triples"] > 0
    assert summary["validation_results"] == 11
    assert len(quarantine) == 11
    assert len(report) > 0
    invalid_po = URIRef("https://example.org/enterprise-context/purchase-order/PO-5001")
    invalid_sources = list(graph.objects(invalid_po, PROV.wasDerivedFrom))
    assert invalid_sources == []
