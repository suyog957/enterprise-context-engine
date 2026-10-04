from __future__ import annotations

import argparse
import json
from pathlib import Path

from enterprise_context.config import get_settings
from enterprise_context.graph.build import build_context_graph
from enterprise_context.graph.query import FusekiGraphStore

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and validate the RDF context graph.")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Replace the local Fuseki default graph",
    )
    args = parser.parse_args()
    summary = build_context_graph()
    print(json.dumps(summary, indent=2))
    if args.upload:
        graph_path = Path(summary["graph_path"])
        settings = get_settings()
        FusekiGraphStore(
            settings.fuseki_url,
            admin_user=settings.fuseki_admin_user,
            admin_password=settings.fuseki_admin_password,
        ).replace_default_graph(graph_path.read_bytes())
        print("Published the SHACL-validated graph to Fuseki.")
    if summary["validation_results"]:
        print(
            "Graph built with quarantined SHACL violations; "
            "inspect the report and quarantine output."
        )


if __name__ == "__main__":
    main()
