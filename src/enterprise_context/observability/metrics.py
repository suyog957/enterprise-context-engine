"""Application and HTTP metrics exposed on /metrics (Prometheus text format)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

HTTP_REQUESTS = Counter(
    "ecg_http_requests_total", "HTTP requests by route template.", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "ecg_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ["method", "route"],
    buckets=LATENCY_BUCKETS,
)
STORE_LATENCY = Histogram(
    "ecg_store_latency_seconds",
    "Latency of calls to backing stores and services.",
    ["store", "operation"],
    buckets=LATENCY_BUCKETS,
)
STORE_ERRORS = Counter(
    "ecg_store_errors_total", "Failed calls to backing stores and services.", ["store", "operation"]
)
TOOL_CALLS = Counter("ecg_tool_calls_total", "Agent tool invocations.", ["tool", "outcome"])
TOOL_LATENCY = Histogram(
    "ecg_tool_latency_seconds", "Agent tool latency.", ["tool"], buckets=LATENCY_BUCKETS
)
POLICY_DECISIONS = Counter(
    "ecg_policy_decisions_total", "OPA decisions by outcome.", ["action", "outcome"]
)
POLICY_DENIAL_REASONS = Counter(
    "ecg_policy_denial_reasons_total", "Reason codes on non-allowed decisions.", ["reason"]
)
AGENT_RUNS = Counter("ecg_agent_runs_total", "Agent workflow runs.", ["intent", "outcome"])
AGENT_STEPS = Histogram(
    "ecg_agent_steps", "Workflow nodes executed per run.", buckets=(2, 4, 6, 8, 10, 12, 16)
)
AGENT_RETRIES = Counter("ecg_agent_retries_total", "Bounded tool retries.", ["tool"])
LLM_TOKENS = Counter("ecg_llm_tokens_total", "LLM tokens.", ["provider", "kind"])
