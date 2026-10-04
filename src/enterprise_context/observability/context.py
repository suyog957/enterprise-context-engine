"""Per-request correlation identifiers shared by logging, audit and tracing."""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

from opentelemetry import trace

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def current_request_id() -> str:
    """The inbound request ID, or a fresh one for work outside an HTTP request."""
    return _request_id.get() or str(uuid4())


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
