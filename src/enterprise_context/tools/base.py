"""Typed tool registry: allowlist, validation, timeouts, bounded retries, telemetry.

Tools are thin typed wrappers over server-side services. Authorization happens inside
those services using the principal from ``ToolContext``; the model only ever chooses
a tool name and arguments, both validated here before anything runs.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Callable, Collection, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from enterprise_context.observability.logging import redact
from enterprise_context.observability.metrics import AGENT_RETRIES, TOOL_CALLS, TOOL_LATENCY
from enterprise_context.observability.tracing import traced
from enterprise_context.security.principals import PrincipalContext


class ToolStatus(StrEnum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    DENIED = "DENIED"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    NOT_ALLOWED = "NOT_ALLOWED"
    ERROR = "ERROR"


class ToolFailure(Exception):
    """Raised by tool handlers to report a classified, non-exceptional outcome."""

    def __init__(self, status: ToolStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ToolContext:
    principal: PrincipalContext


class ToolInvocation(BaseModel):
    tool: str
    arguments: dict[str, Any]
    status: ToolStatus
    duration_ms: float
    attempts: int
    error: str | None = None
    output: Any = None

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.OK


Handler = Callable[[Any, ToolContext], BaseModel]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Handler
    read_only: bool = True
    timeout_seconds: float = 8.0
    # Exception types mapped to an outcome; transient ones are retried.
    error_map: Mapping[type[Exception], ToolStatus] = field(default_factory=dict)
    transient: tuple[type[Exception], ...] = ()


class ToolRegistry:
    def __init__(
        self, tools: Collection[Tool], *, max_retries: int = 1, max_workers: int = 4
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._max_retries = max_retries
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tool")

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self, allowed: Collection[str] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "read_only": tool.read_only,
                "parameters": tool.input_model.model_json_schema(),
            }
            for name, tool in sorted(self._tools.items())
            if allowed is None or name in allowed
        ]

    def _classify(self, tool: Tool, error: Exception) -> ToolStatus:
        if isinstance(error, ToolFailure):
            return error.status
        for error_type, status in tool.error_map.items():
            if isinstance(error, error_type):
                return status
        if isinstance(error, tool.transient):
            return ToolStatus.UNAVAILABLE
        return ToolStatus.ERROR

    def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        *,
        allowed: Collection[str] | None = None,
    ) -> ToolInvocation:
        started = time.perf_counter()
        safe_arguments = redact(dict(arguments))

        def finish(
            status: ToolStatus, attempts: int, *, error: str | None = None, output: Any = None
        ) -> ToolInvocation:
            elapsed = time.perf_counter() - started
            TOOL_CALLS.labels(tool=name, outcome=status.value).inc()
            TOOL_LATENCY.labels(tool=name).observe(elapsed)
            return ToolInvocation(
                tool=name,
                arguments=safe_arguments,
                status=status,
                duration_ms=round(elapsed * 1000, 2),
                attempts=attempts,
                error=error,
                output=output,
            )

        tool = self._tools.get(name)
        if tool is None or (allowed is not None and name not in allowed):
            return finish(ToolStatus.NOT_ALLOWED, 0, error=f"Tool '{name}' is not permitted")
        try:
            parsed = tool.input_model.model_validate(dict(arguments))
        except ValidationError as error:
            return finish(ToolStatus.INVALID, 0, error=error.errors(include_url=False)[0]["msg"])

        attempts = 0
        with traced(f"tool.{name}", **{"ecg.tool": name, "ecg.read_only": tool.read_only}) as span:
            while True:
                attempts += 1
                call_context = contextvars.copy_context()
                future = self._executor.submit(call_context.run, tool.handler, parsed, context)
                try:
                    output = future.result(timeout=tool.timeout_seconds)
                except FutureTimeout:
                    future.cancel()
                    span.set_attribute("ecg.outcome", ToolStatus.TIMEOUT.value)
                    return finish(
                        ToolStatus.TIMEOUT,
                        attempts,
                        error=f"Tool exceeded {tool.timeout_seconds}s timeout",
                    )
                except Exception as error:  # classified below; never escapes the agent
                    status = self._classify(tool, error)
                    if (
                        status is ToolStatus.UNAVAILABLE
                        and isinstance(error, tool.transient)
                        and attempts <= self._max_retries
                    ):
                        AGENT_RETRIES.labels(tool=name).inc()
                        continue
                    span.set_attribute("ecg.outcome", status.value)
                    return finish(status, attempts, error=str(error) or type(error).__name__)
                span.set_attribute("ecg.outcome", ToolStatus.OK.value)
                return finish(ToolStatus.OK, attempts, output=output.model_dump(mode="json"))
