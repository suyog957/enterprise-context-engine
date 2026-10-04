from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote

from owlrl import DeductiveClosure, RDFS_Semantics
from pyshacl import validate
from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

from enterprise_context.config import get_settings

ECG = Namespace("https://example.org/enterprise-context#")
PROV = Namespace("http://www.w3.org/ns/prov#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
CATEGORY_TERMS = {
    "Software": ECG.Software,
    "Hardware": ECG.Hardware,
    "Cloud Services": ECG.CloudServices,
    "Consulting": ECG.Consulting,
    "Legal": ECG.Legal,
    "Accounting": ECG.Accounting,
    "Office Supplies": ECG.OfficeSupplies,
    "Maintenance": ECG.Maintenance,
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def _uri(kind: str, identifier: str) -> URIRef:
    return URIRef(f"https://example.org/enterprise-context/{kind}/{quote(identifier, safe='')}")


def _category_uri(category: str) -> URIRef:
    return CATEGORY_TERMS.get(category, ECG["Category_" + quote(category, safe="")])


def _apply_subclass_inference(graph: Graph) -> int:
    schema = Graph()
    class_nodes: set[URIRef] = set()
    for subclass, superclass in graph.subject_objects(RDFS.subClassOf):
        schema.add((subclass, RDFS.subClassOf, superclass))
        if isinstance(subclass, URIRef):
            class_nodes.add(subclass)
        if isinstance(superclass, URIRef):
            class_nodes.add(superclass)

    for subject, class_uri in graph.subject_objects(RDF.type):
        if isinstance(subject, URIRef) and subject not in class_nodes:
            schema.add((subject, RDF.type, class_uri))

    DeductiveClosure(RDFS_Semantics).expand(schema)
    inferred_types = [
        triple
        for triple in schema.triples((None, RDF.type, None))
        if triple not in graph
    ]
    for triple in inferred_types:
        graph.add(triple)
    return len(inferred_types)


def _source_assertion(graph: Graph, subject: URIRef, record: dict[str, Any]) -> None:
    source_id = str(record.get("source_record_id", "unknown"))
    source_system = str(record.get("source_system", "unknown"))
    assertion = _uri("source-record", f"{source_system}:{source_id}")
    graph.add((assertion, RDF.type, ECG.SourceRecord))
    graph.add((assertion, ECG.hasSourceSystem, Literal(source_system)))
    graph.add((assertion, ECG.hasSourceRecordId, Literal(source_id)))
    observed_at = record.get("observed_at")
    if observed_at:
        graph.add((assertion, ECG.observedAt, Literal(observed_at, datatype=XSD.dateTime)))
    if record.get("supplier_name"):
        graph.add((assertion, ECG.sourceName, Literal(record["supplier_name"])))
    graph.add((subject, PROV.wasDerivedFrom, assertion))


def _add_supplier_records(graph: Graph, records: list[dict[str, Any]]) -> dict[str, URIRef]:
    source_supplier_nodes: dict[str, URIRef] = {}
    preferred_names: dict[str, str] = {}
    for record in records:
        if record.get("source_system") == "ERP":
            preferred_names[str(record.get("canonical_entity_id"))] = str(record["supplier_name"])

    for record in records:
        source_supplier_id = str(record.get("source_supplier_id", ""))
        resolved_entity_id = record.get("canonical_entity_id")
        if not resolved_entity_id:
            continue
        if record.get("resolution_decision") == "review":
            candidate = _uri(
                "supplier-candidate", str(record.get("source_record_id", source_supplier_id))
            )
            graph.add((candidate, RDF.type, ECG.EntityResolutionCandidate))
            graph.add((candidate, ECG.hasAlias, Literal(str(record["supplier_name"]))))
            graph.add(
                (
                    candidate,
                    ECG.confidenceScore,
                    Literal(record.get("confidence_score", 0), datatype=XSD.decimal),
                )
            )
            proposed_id = record.get("candidate_entity_id")
            if proposed_id:
                graph.add(
                    (candidate, ECG.proposedCanonicalEntity, _uri("supplier", str(proposed_id)))
                )
            _source_assertion(graph, candidate, record)
            continue
        supplier = _uri("supplier", str(resolved_entity_id))
        source_supplier_nodes[source_supplier_id] = supplier
        graph.add((supplier, RDF.type, ECG.Supplier))
        graph.add((supplier, ECG.canonicalId, Literal(str(resolved_entity_id))))
        if (supplier, ECG.displayName, None) not in graph:
            display_name = preferred_names.get(
                str(resolved_entity_id), str(record["supplier_name"])
            )
            graph.add((supplier, ECG.displayName, Literal(display_name)))
        graph.add((supplier, ECG.hasAlias, Literal(str(record["supplier_name"]))))
        status = record.get("status") or "UNDER_REVIEW"
        graph.add((supplier, ECG.hasSupplierStatus, ECG[str(status)]))
        graph.add((supplier, ECG.riskRating, Literal(str(record.get("risk_rating") or "UNKNOWN"))))
        category = str(record.get("category", ""))
        if category:
            graph.add((supplier, ECG.hasApprovedCategory, _category_uri(category)))
        _source_assertion(graph, supplier, record)
    return source_supplier_nodes


def build_context_graph(
    data_root: Path | None = None, ontology_root: Path | None = None
) -> dict[str, Any]:
    settings = get_settings()
    data_root = data_root or settings.data_dir
    ontology_root = ontology_root or settings.ontology_dir
    raw_root = data_root / "raw" / "generated"
    canonical_root = data_root / "canonical" / "generated"
    output_root = canonical_root
    output_root.mkdir(parents=True, exist_ok=True)

    graph = Graph()
    graph.bind("ecg", ECG)
    graph.bind("prov", PROV)
    graph.bind("skos", SKOS)
    graph.parse(ontology_root / "enterprise.ttl", format="turtle")
    graph.parse(ontology_root / "taxonomy.ttl", format="turtle")

    resolution_results = _read_jsonl(canonical_root / "entity_resolution.jsonl")
    resolution_by_source = {
        str(row["source_record_id"]): row for row in resolution_results
    }
    supplier_records = _read_jsonl(raw_root / "supplier_source_records.jsonl")
    for record in supplier_records:
        result = resolution_by_source.get(str(record["source_record_id"]))
        if result:
            record["canonical_entity_id"] = result["canonical_entity_id"]
            record["confidence_score"] = result["confidence_score"]
            record["resolution_method"] = result["resolution_method"]
            record["resolution_decision"] = result["decision"]
            record["candidate_entity_id"] = result.get("candidate_entity_id")

    supplier_by_source_key = _add_supplier_records(graph, supplier_records)
    for record in supplier_records:
        source_assertion = _uri(
            "source-record", f"{record['source_system']}:{record['source_record_id']}"
        )
        resolution = resolution_by_source.get(str(record["source_record_id"]))
        if resolution:
            graph.add(
                (
                    source_assertion,
                    ECG.confidenceScore,
                    Literal(resolution["confidence_score"], datatype=XSD.decimal),
                )
            )
            graph.add(
                (source_assertion, ECG.resolutionMethod, Literal(resolution["resolution_method"]))
            )
    buyer_nodes: dict[str, URIRef] = {}
    for record in _read_jsonl(raw_root / "buyers.jsonl"):
        buyer_node = _uri("buyer", str(record["buyer_id"]))
        buyer_nodes[str(record["buyer_id"])] = buyer_node
        graph.add((buyer_node, RDF.type, ECG.Buyer))
        graph.add((buyer_node, ECG.canonicalId, Literal(record["buyer_id"])))
        graph.add((buyer_node, ECG.displayName, Literal(record["name"])))
        graph.add(
            (
                buyer_node,
                ECG.approvalLimit,
                Literal(Decimal(record["approval_limit"]), datatype=XSD.decimal),
            )
        )
        business_unit = _uri("business-unit", str(record["business_unit_id"]))
        graph.add((buyer_node, ECG.belongsToBusinessUnit, business_unit))
        _source_assertion(graph, buyer_node, record)

    product_nodes: dict[str, URIRef] = {}
    for record in _read_jsonl(raw_root / "products.jsonl"):
        product_node = _uri("product", str(record["product_id"]))
        product_nodes[str(record["product_id"])] = product_node
        graph.add((product_node, RDF.type, ECG.Product))
        graph.add((product_node, ECG.canonicalId, Literal(record["product_id"])))
        graph.add((product_node, ECG.displayName, Literal(record["name"])))
        category_uri = _category_uri(str(record["category"]))
        graph.add((category_uri, RDF.type, ECG.ProductCategory))
        graph.add((category_uri, SKOS.prefLabel, Literal(record["category"], lang="en")))
        graph.add((product_node, ECG.hasCategory, category_uri))
        _source_assertion(graph, product_node, record)

    process_states = {
        "DRAFT": ECG.DRAFT,
        "SUBMITTED": ECG.SUBMITTED,
        "APPROVED": ECG.APPROVED,
        "REJECTED": ECG.REJECTED,
        "CLOSED": ECG.CLOSED,
        "CONVERTED": ECG.CONVERTED,
    }
    requisition_nodes: dict[str, URIRef] = {}
    for record in _read_jsonl(raw_root / "purchase_requisitions.jsonl"):
        requisition_id = str(record["requisition_id"])
        requisition = _uri("requisition", requisition_id)
        requisition_nodes[requisition_id] = requisition
        graph.add((requisition, RDF.type, ECG.PurchaseRequisition))
        graph.add((requisition, ECG.canonicalId, Literal(requisition_id)))
        graph.add((requisition, ECG.displayName, Literal(requisition_id)))
        graph.add((requisition, ECG.hasState, process_states[str(record["state"])]))
        requisition_supplier = supplier_by_source_key.get(str(record["supplier_id"]))
        if requisition_supplier is not None:
            graph.add((requisition, ECG.hasSupplier, requisition_supplier))
        requisition_buyer = buyer_nodes.get(str(record["buyer_id"]))
        if requisition_buyer is not None:
            graph.add((requisition, ECG.ownedByBuyer, requisition_buyer))
        business_unit = _uri("business-unit", str(record["business_unit_id"]))
        graph.add((requisition, ECG.belongsToBusinessUnit, business_unit))
        amount = Decimal(str(record["amount"]))
        graph.add((requisition, ECG.amount, Literal(amount, datatype=XSD.decimal)))
        graph.add((requisition, ECG.currency, Literal(str(record["currency"]))))
        created_at = record.get("created_at")
        if created_at:
            graph.add((requisition, ECG.createdAt, Literal(created_at, datatype=XSD.dateTime)))
        for product_id in record.get("product_ids", []):
            requisition_product = product_nodes.get(str(product_id))
            if requisition_product is not None:
                graph.add((requisition, ECG.containsProduct, requisition_product))
        _source_assertion(graph, requisition, record)

    purchase_order_records = _read_jsonl(raw_root / "purchase_orders.jsonl")
    for record in purchase_order_records:
        purchase_order = _uri("purchase-order", str(record["purchase_order_id"]))
        graph.add((purchase_order, RDF.type, ECG.PurchaseOrder))
        graph.add((purchase_order, ECG.canonicalId, Literal(record["purchase_order_id"])))
        graph.add((purchase_order, ECG.displayName, Literal(record["purchase_order_id"])))
        order_supplier = supplier_by_source_key.get(str(record["supplier_id"]))
        if order_supplier is not None:
            graph.add((purchase_order, ECG.hasSupplier, order_supplier))
        order_buyer = buyer_nodes.get(str(record["buyer_id"]))
        if order_buyer is not None:
            graph.add((purchase_order, ECG.ownedByBuyer, order_buyer))
        source_requisition = requisition_nodes.get(str(record.get("requisition_id", "")))
        if source_requisition is not None:
            graph.add((purchase_order, ECG.createdFrom, source_requisition))
        graph.add((purchase_order, ECG.currency, Literal(str(record["currency"]))))
        graph.add((purchase_order, ECG.displayStatus, Literal(str(record["status"]))))
        if record.get("amount") is not None:
            try:
                po_amount: Decimal | None = Decimal(str(record["amount"]))
            except InvalidOperation:
                po_amount = None
            if po_amount is not None:
                graph.add((purchase_order, ECG.amount, Literal(po_amount, datatype=XSD.decimal)))
        _source_assertion(graph, purchase_order, record)

    contract_records = _read_jsonl(raw_root / "contracts.jsonl")
    for record in contract_records:
        contract = _uri("contract", str(record["contract_id"]))
        graph.add((contract, RDF.type, ECG.Contract))
        graph.add((contract, ECG.canonicalId, Literal(record["contract_id"])))
        contract_supplier = supplier_by_source_key.get(str(record["supplier_id"]))
        if contract_supplier is not None:
            graph.add((contract, ECG.hasSupplier, contract_supplier))
        graph.add((contract, ECG.startDate, Literal(record["start_date"], datatype=XSD.date)))
        graph.add((contract, ECG.endDate, Literal(record["end_date"], datatype=XSD.date)))
        graph.add((contract, ECG.displayStatus, Literal(record["status"])))
        for category in record.get("permitted_categories", []):
            graph.add((contract, ECG.permitsCategory, _category_uri(str(category))))
        _source_assertion(graph, contract, record)

    pre_inference_size = len(graph)
    inferred_triple_count = _apply_subclass_inference(graph)

    shapes = Graph().parse(ontology_root / "shapes.ttl", format="turtle")
    conforms, report_graph, report_text = validate(graph, shacl_graph=shapes, inference="none")
    violations: list[dict[str, str]] = []
    for result_node in report_graph.subjects(RDF.type, Namespace("http://www.w3.org/ns/shacl#").ValidationResult):
        focus = report_graph.value(result_node, Namespace("http://www.w3.org/ns/shacl#").focusNode)
        message = report_graph.value(result_node, Namespace("http://www.w3.org/ns/shacl#").resultMessage)
        source_records = (
            [str(source) for source in graph.objects(focus, PROV.wasDerivedFrom)]
            if focus
            else []
        )
        violations.append(
            {
                "focus_node": str(focus or ""),
                "source_records": ",".join(source_records),
                "message": str(message or "SHACL validation failure"),
            }
        )

    valid_graph = Graph()
    for prefix, namespace in graph.namespaces():
        valid_graph.bind(prefix, namespace)
    quarantined_nodes = {
        URIRef(row["focus_node"]) for row in violations if row["focus_node"]
    }
    for focus_node in list(quarantined_nodes):
        quarantined_nodes.update(
            source
            for source in graph.objects(focus_node, PROV.wasDerivedFrom)
            if isinstance(source, URIRef)
        )
    for triple in graph:
        if triple[0] not in quarantined_nodes and triple[2] not in quarantined_nodes:
            valid_graph.add(triple)

    graph_path = output_root / "context_graph.ttl"
    report_path = output_root / "shacl_report.ttl"
    quarantine_path = output_root / "shacl_quarantine.jsonl"
    summary_path = output_root / "graph_build_summary.json"
    valid_graph.serialize(destination=graph_path, format="turtle")
    report_graph.serialize(destination=report_path, format="turtle")
    with quarantine_path.open("w", encoding="utf-8", newline="\n") as output:
        for violation in violations:
            output.write(json.dumps(violation, sort_keys=True) + "\n")

    summary = {
        "conforms": bool(conforms),
        "input_triples": pre_inference_size,
        "inferred_triples": inferred_triple_count,
        "output_triples": len(valid_graph),
        "quarantined_nodes": len(quarantined_nodes),
        "validation_results": len(violations),
        "shacl_report": report_text,
        "graph_path": str(graph_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
