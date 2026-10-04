from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from enterprise_context.domain.models import GoldenEntityPair, SupplierSourceRecord
from enterprise_context.entity_resolution.evaluation import evaluate_entity_resolution
from enterprise_context.entity_resolution.resolver import resolve_supplier_records

ROOT = Path(__file__).resolve().parents[1]


def read_models(path: Path, model_type: type[Any]) -> list[Any]:
    with path.open(encoding="utf-8") as source:
        return [model_type.model_validate_json(line) for line in source if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve synthetic supplier source records.")
    parser.add_argument(
        "--input", type=Path, default=ROOT / "data/raw/generated/supplier_source_records.jsonl"
    )
    parser.add_argument(
        "--golden", type=Path, default=ROOT / "data/golden/generated/entity_pairs.jsonl"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/canonical/generated"
    )
    args = parser.parse_args()

    source_records = read_models(args.input, SupplierSourceRecord)
    golden_pairs = read_models(args.golden, GoldenEntityPair)
    resolutions = resolve_supplier_records(source_records)
    metrics = evaluate_entity_resolution(golden_pairs, resolutions)

    args.output.mkdir(parents=True, exist_ok=True)
    results_path = args.output / "entity_resolution.jsonl"
    with results_path.open("w", encoding="utf-8", newline="\n") as output:
        for result in resolutions:
            output.write(result.model_dump_json() + "\n")

    metrics_path = args.output / "entity_resolution_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"resolved_records": len(resolutions), "metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()
