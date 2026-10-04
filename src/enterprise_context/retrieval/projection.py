"""Versioned search-index publication behind a stable alias."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from typing import Any

import psycopg

from enterprise_context.projection.state import lock_and_next_version, record_published_version
from enterprise_context.retrieval.models import IndexedDocument
from enterprise_context.retrieval.opensearch import (
    VERSIONED_INDEX_PREFIX,
    OpenSearchError,
    OpenSearchHybridRetriever,
)

SEARCH_PROJECTION = "search_index"
logger = logging.getLogger(__name__)


def documents_hash(documents: Sequence[IndexedDocument]) -> str:
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: item.document_id):
        digest.update(document.model_dump_json().encode("utf-8"))
    return digest.hexdigest()


def publish_search_version(
    retriever: OpenSearchHybridRetriever,
    connection: psycopg.Connection[Any],
    documents: Sequence[IndexedDocument],
) -> dict[str, Any]:
    """Build a new index version, switch the alias atomically, then drop old versions."""
    with connection.transaction():
        version = lock_and_next_version(connection, SEARCH_PROJECTION)
        index_name = f"{VERSIONED_INDEX_PREFIX}{version}"
        retriever.ensure_index(index_name)
        indexed = retriever.index_documents(documents, index_name)
        retired = retriever.switch_alias(index_name)
        record_published_version(
            connection,
            SEARCH_PROJECTION,
            version,
            index_name,
            content_hash=documents_hash(documents),
            item_count=indexed,
        )
    for index in retired:
        try:
            retriever.delete_index(index)
        except OpenSearchError:
            logger.warning("Could not delete retired index %s", index)
    return {
        "index": index_name,
        "version": version,
        "indexed_documents": indexed,
        "retired_indices": retired,
    }
