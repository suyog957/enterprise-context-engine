"""OpenTelemetry setup and a helper that records a span plus store metrics together.

Tracing is exported over OTLP/HTTP (Jaeger accepts it on :4318) only when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is configured; otherwise spans are no-ops, which
keeps unit tests and scripts free of exporter side effects.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from enterprise_context.observability.metrics import STORE_ERRORS, STORE_LATENCY

_TRACER_NAME = "enterprise_context"
_configured = False


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def configure_tracing(
    *, service_name: str, service_version: str, environment: str, otlp_endpoint: str | None
) -> bool:
    """Install the SDK tracer provider and library instrumentations once per process."""
    global _configured
    if _configured or not otlp_endpoint:
        return _configured
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
                "deployment.environment": environment,
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    PsycopgInstrumentor().instrument(enable_commenter=False)
    _configured = True
    return True


def _clean(attributes: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            cleaned[key] = value
        elif isinstance(value, (list, tuple)):
            cleaned[key] = [str(item) for item in value]
        else:
            cleaned[key] = str(value)
    return cleaned


@contextmanager
def traced(name: str, **attributes: Any) -> Iterator[Span]:
    """Start a span; exceptions mark it as errored and propagate."""
    with get_tracer().start_as_current_span(name, attributes=_clean(attributes)) as span:
        try:
            yield span
        except Exception as error:
            span.set_status(Status(StatusCode.ERROR, type(error).__name__))
            span.record_exception(error)
            raise


@contextmanager
def observed_store_call(store: str, operation: str, **attributes: Any) -> Iterator[Span]:
    """Span + latency histogram + error counter for one call to a backing store."""
    started = time.perf_counter()
    try:
        with traced(f"{store}.{operation}", **{"ecg.store": store, **attributes}) as span:
            yield span
    except Exception:
        STORE_ERRORS.labels(store=store, operation=operation).inc()
        raise
    finally:
        STORE_LATENCY.labels(store=store, operation=operation).observe(
            time.perf_counter() - started
        )
