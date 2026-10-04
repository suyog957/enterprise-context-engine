from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import psycopg

from enterprise_context.config import get_settings
from enterprise_context.graph.build import build_context_graph
from enterprise_context.graph.projection import publish_graph_version
from enterprise_context.graph.query import FusekiGraphStore
from enterprise_context.projection.worker import GraphProjector, replay_events


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
        projector = GraphProjector(store)
        replayed: list[int] = []
        with psycopg.connect(settings.database_url, autocommit=True) as connection:

            def replay(graph_uri: str) -> datetime | None:
                # Committed platform writes are replayed from the outbox so a rebuild
                # from source files never loses them.
                count, watermark = replay_events(connection, projector, graph_uri)
                replayed.append(count)
                return watermark

            version = publish_graph_version(
                store,
                connection,
                Path(summary["ntriples_path"]).read_bytes(),
                expected_triples=int(summary["output_triples"]),
                content_hash=str(summary["content_hash"]),
                before_switch=replay,
            )
        print(
            json.dumps(
                {
                    "published_graph": version.target_uri,
                    "version": version.version,
                    "triples": version.item_count,
                    "replayed_events": sum(replayed),
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
