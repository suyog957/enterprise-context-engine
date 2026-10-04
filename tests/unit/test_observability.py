import logging

import pytest
from fastapi.testclient import TestClient

from enterprise_context.api import app
from enterprise_context.observability.jaeger import summarize_jaeger_trace
from enterprise_context.observability.logging import (
    REDACTED,
    CorrelationFilter,
    RedactingJsonFormatter,
    redact,
)


def test_redaction_masks_sensitive_keys_and_inline_secrets() -> None:
    value = {
        "Authorization": "Bearer abc",
        "nested": {"password": "pw", "note": "token=xyz rest"},
        "url": "postgresql://context_app:secretpw@postgres:5432/db",
        "safe": "PR-1007",
    }
    redacted = redact(value)

    assert redacted["Authorization"] == REDACTED
    assert redacted["nested"]["password"] == REDACTED
    assert redacted["nested"]["note"] == f"token={REDACTED} rest"
    assert redacted["url"] == f"postgresql://context_app:{REDACTED}@postgres:5432/db"
    assert redacted["safe"] == "PR-1007"


def test_json_formatter_emits_correlation_fields() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello password=pw", None, None)
    CorrelationFilter().filter(record)
    output = RedactingJsonFormatter("%(message)s %(request_id)s %(trace_id)s").format(record)

    assert '"request_id"' in output
    assert "pw" not in output


def test_jaeger_payload_is_summarized_with_parent_links_and_relative_offsets() -> None:
    payload = {
        "data": [
            {
                "processes": {"p1": {"serviceName": "enterprise-context-api"}},
                "spans": [
                    {
                        "spanID": "b",
                        "operationName": "opa.evaluate",
                        "processID": "p1",
                        "startTime": 1_500,
                        "duration": 2_000,
                        "references": [{"refType": "CHILD_OF", "spanID": "a"}],
                        "tags": [{"key": "ecg.store", "value": "opa"}, {"key": "x", "value": 1}],
                    },
                    {
                        "spanID": "a",
                        "operationName": "POST /chat",
                        "processID": "p1",
                        "startTime": 1_000,
                        "duration": 9_000,
                        "references": [],
                        "tags": [],
                    },
                ],
            }
        ]
    }
    spans = summarize_jaeger_trace(payload)

    assert [span["operation"] for span in spans] == ["POST /chat", "opa.evaluate"]
    assert spans[1]["parent_span_id"] == "a"
    assert spans[1]["start_offset_ms"] == 0.5
    assert spans[1]["tags"] == {"ecg.store": "opa"}


def test_request_id_is_propagated_or_replaced_when_unsafe() -> None:
    client = TestClient(app)
    kept = client.get("/health/live", headers={"x-request-id": "req-123"})
    replaced = client.get("/health/live", headers={"x-request-id": "bad id <script>"})

    assert kept.headers["x-request-id"] == "req-123"
    assert replaced.headers["x-request-id"] != "bad id <script>"


@pytest.mark.parametrize("path", ["/metrics"])
def test_application_metrics_are_exposed(path: str) -> None:
    body = TestClient(app).get(path).text
    assert "ecg_store_latency_seconds" in body
