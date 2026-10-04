from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg

from enterprise_context.config import get_settings
from enterprise_context.graph.build import build_context_graph
from enterprise_context.graph.projection import publish_graph_version
from enterprise_context.graph.query import FusekiGraphStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and validate the RDF context graph.")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Publish the graph to Fuseki as a new versioned named graph",
    )
    args = parser.parse_args()
    summary = build_context_graph()
    summary.pop("shacl_report", None)
    print(json.dumps(summary, indent=2))
    if args.upload:
        settings = get_settings()
        store = FusekiGraphStore(
            settings.fuseki_url,
            admin_user=settings.fuseki_admin_user,
            admin_password=settings.fuseki_admin_password,
        )
        with psycopg.connect(settings.database_url, autocommit=True) as connection:
            version = publish_graph_version(
                store,
                connection,
                Path(summary["ntriples_path"]).read_bytes(),
                expected_triples=int(summary["output_triples"]),
                content_hash=str(summary["content_hash"]),
            )
        print(
            json.dumps(
                {
                    "published_graph": version.target_uri,
                    "version": version.version,
                    "triples": version.item_count,
                }
            )
        )
    if summary["validation_results"]:
        print(
            "Graph built with quarantined SHACL violations; "
            "inspect the report and quarantine output."
        )


if __name__ == "__main__":
    main()
