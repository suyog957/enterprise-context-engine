"""Server-side lookup of a trace in Jaeger's query API, reduced to a span summary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

_KEPT_TAGS = {
    "error",
    "http.method",
    "http.route",
    "http.status_code",
    "ecg.store",
    "ecg.tool",
    "ecg.intent",
    "ecg.node",
    "ecg.outcome",
    "db.system",
    "otel.status_code",
}


@dataclass
class TraceSummary:
    source: str
    spans: list[dict[str, Any]] = field(default_factory=list)


def summarize_jaeger_trace(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or []
    if not data:
        return []
    trace = data[0]
    processes = trace.get("processes", {})
    spans = trace.get("spans", [])
    start = min((int(span.get("startTime", 0)) for span in spans), default=0)
    summary: list[dict[str, Any]] = []
    for span in sorted(spans, key=lambda item: int(item.get("startTime", 0))):
        parent = next(
            (
                ref.get("spanID")
                for ref in span.get("references", [])
                if ref.get("refType") == "CHILD_OF"
            ),
            None,
        )
        tags = {
            tag["key"]: tag.get("value")
            for tag in span.get("tags", [])
            if tag.get("key") in _KEPT_TAGS
        }
        summary.append(
            {
                "span_id": span.get("spanID"),
                "parent_span_id": parent,
                "operation": span.get("operationName"),
                "service": processes.get(span.get("processID"), {}).get("serviceName"),
                "start_offset_ms": round((int(span.get("startTime", 0)) - start) / 1000, 2),
                "duration_ms": round(int(span.get("duration", 0)) / 1000, 2),
                "tags": tags,
            }
        )
    return summary


def fetch_trace_summary(
    jaeger_query_url: str | None, trace_id: str, *, client: httpx.Client | None = None
) -> TraceSummary:
    """Best effort: an unavailable Jaeger yields an empty summary, never an error."""
    if not jaeger_query_url:
        return TraceSummary(source="disabled")
    owns_client = client is None
    http = client or httpx.Client(timeout=2.0)
    try:
        response = http.get(f"{jaeger_query_url.rstrip('/')}/api/traces/{trace_id}")
        if response.status_code == 404:
            return TraceSummary(source="not_found")
        response.raise_for_status()
        return TraceSummary(source="jaeger", spans=summarize_jaeger_trace(response.json()))
    except (httpx.HTTPError, ValueError):
        return TraceSummary(source="unavailable")
    finally:
        if owns_client:
            http.close()
