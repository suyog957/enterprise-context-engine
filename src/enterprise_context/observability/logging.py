"""Structured JSON logging with trace correlation and secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from pythonjsonlogger.json import JsonFormatter

from enterprise_context.observability.context import _request_id, current_trace_id

_SENSITIVE_KEYS = re.compile(
    r"(authorization|password|secret|token|api[_-]?key|idempotency[_-]?key|cookie)", re.I
)
_INLINE_SECRET = re.compile(
    r"(?i)((?:password|secret|token|api[_-]?key)\s*[=:]\s*)([^\s,;&]+)|"
    r"(postgres(?:ql)?://[^:/\s]+:)([^@\s]+)(@)"
)
REDACTED = "[REDACTED]"


def redact_text(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group(1):
            return f"{match.group(1)}{REDACTED}"
        return f"{match.group(3)}{REDACTED}{match.group(5)}"

    return _INLINE_SECRET.sub(replace, value)


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEYS.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.trace_id = current_trace_id()
        return True


class RedactingJsonFormatter(JsonFormatter):
    def process_log_record(self, log_record: dict[str, Any]) -> dict[str, Any]:
        return {key: redact(value, key) for key, value in log_record.items()}


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(
        RedactingJsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s %(trace_id)s",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Route uvicorn's own loggers through the JSON handler.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
