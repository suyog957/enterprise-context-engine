"""Thread-safe access to rdflib's SPARQL parser.

rdflib's pyparsing-based grammar keeps shared parser state and is not thread-safe:
concurrent parses (FastAPI runs sync endpoints in a thread pool) intermittently fail
valid queries. Parsing takes milliseconds, so it is serialized behind one lock; query
execution against Fuseki happens outside the lock.
"""

from __future__ import annotations

import threading
from typing import Any

from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parser import parseQuery, parseUpdate
from rdflib.plugins.sparql.sparql import Query

_PARSE_LOCK = threading.Lock()


def parse_query(query: str) -> Any:
    with _PARSE_LOCK:
        return parseQuery(query)


def parse_update(update: str) -> Any:
    with _PARSE_LOCK:
        return parseUpdate(update)


def prepare_query(query: str) -> Query:
    with _PARSE_LOCK:
        return prepareQuery(query)
