"""Rebuild read projections from authoritative sources.

- Graph: rebuilt from versioned source data + ontology, then every committed outbox
  event is replayed onto the new version before it becomes current.
- Search: re-indexed into a new versioned index; the alias switches atomically.
- --process-outbox: drain pending outbox events once (what the projector service does).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from enterprise_context.projection.worker import build_worker

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", action="store_true", help="Rebuild the RDF projection")
    parser.add_argument("--search", action="store_true", help="Rebuild the search index")
    parser.add_argument("--process-outbox", action="store_true", help="Drain pending events")
    args = parser.parse_args()
    if not (args.graph or args.search or args.process_outbox):
        args.graph = args.search = True
    if args.graph:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_graph.py"), "--upload"], check=True
        )
    if args.search:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_search_index.py")], check=True
        )
    if args.process_outbox:
        worker = build_worker()
        total: dict[str, int] = {}
        while True:
            report = worker.process_batch()
            for key in (
                "claimed",
                "published",
                "skipped_already_applied",
                "failed",
                "dead_lettered",
            ):
                total[key] = total.get(key, 0) + int(getattr(report, key))
            if report.claimed == 0:
                break
        print(json.dumps({"outbox": total}))


if __name__ == "__main__":
    main()
