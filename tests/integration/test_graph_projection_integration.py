"""Live Fuseki + PostgreSQL test for versioned graph publication.

Requires TEST_DATABASE_URL, a dedicated Fuseki test dataset (TEST_FUSEKI_URL) and
FUSEKI_ADMIN_PASSWORD.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from enterprise_context.graph.projection import (
    GRAPH_URI_PREFIX,
    CurrentGraphResolver,
    publish_graph_version,
)
from enterprise_context.graph.query import FusekiGraphStore
from enterprise_context.migrations import apply_migrations

ROOT = Path(__file__).resolve().parents[2]
TRIPLES = b"".join(
    f"<urn:test:s{index}> <urn:test:p> <urn:test:o{index}> .\n".encode() for index in range(250)
)


@pytest.mark.integration
def test_publish_switches_versions_atomically_and_drops_the_previous_graph() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    fuseki_url = os.environ.get("TEST_FUSEKI_URL")
    password = os.environ.get("FUSEKI_ADMIN_PASSWORD")
    if not (database_url and fuseki_url and password):
        pytest.skip("TEST_DATABASE_URL, TEST_FUSEKI_URL and FUSEKI_ADMIN_PASSWORD are required")

    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.transaction():
            apply_migrations(connection, ROOT / "infra" / "sql")
        resolver = CurrentGraphResolver(database_url, ttl_seconds=0)
        reader = FusekiGraphStore(fuseki_url, graph_uri_provider=resolver)
        writer = FusekiGraphStore(fuseki_url, admin_user="admin", admin_password=password)

        first = publish_graph_version(
            writer, connection, TRIPLES, expected_triples=250, content_hash="first"
        )
        second = publish_graph_version(
            writer,
            connection,
            TRIPLES[: TRIPLES.index(b"\n") + 1],
            expected_triples=1,
            content_hash="second",
        )

        assert second.version == first.version + 1
        assert resolver() == second.target_uri
        count = reader.run_readonly_sparql("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o } LIMIT 1")
        assert count.rows == [{"n": "1"}]
        assert writer.list_named_graphs(GRAPH_URI_PREFIX) == [second.target_uri]
