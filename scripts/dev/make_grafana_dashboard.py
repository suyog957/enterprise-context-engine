"""Generate the provisioned Grafana dashboard from code, so panels stay consistent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "infra" / "docker" / "grafana" / "dashboards" / "enterprise-context.json"
NOT_PROBES = '{route!~"/health.*|/metrics"}'

PANELS: list[tuple[str, str, str, str]] = [
    (
        "HTTP requests per second by route",
        "timeseries",
        f"sum by (route) (rate(ecg_http_requests_total{NOT_PROBES}[5m]))",
        "reqps",
    ),
    (
        "HTTP p95 latency by route",
        "timeseries",
        "histogram_quantile(0.95, sum by (le, route) "
        f"(rate(ecg_http_request_duration_seconds_bucket{NOT_PROBES}[5m])))",
        "s",
    ),
    (
        "Store p95 latency (SQL, SPARQL, OpenSearch, OPA, LLM)",
        "timeseries",
        "histogram_quantile(0.95, sum by (le, store) (rate(ecg_store_latency_seconds_bucket[5m])))",
        "s",
    ),
    (
        "Store errors (15m)",
        "timeseries",
        "sum by (store, operation) (increase(ecg_store_errors_total[15m]))",
        "short",
    ),
    (
        "Agent runs by outcome (1h)",
        "bargauge",
        "sum by (outcome) (increase(ecg_agent_runs_total[1h]))",
        "short",
    ),
    (
        "Agent runs by intent (1h)",
        "bargauge",
        "sum by (intent) (increase(ecg_agent_runs_total[1h]))",
        "short",
    ),
    (
        "Workflow nodes per run (p50)",
        "timeseries",
        "histogram_quantile(0.5, sum by (le) (rate(ecg_agent_steps_bucket[15m])))",
        "short",
    ),
    (
        "Tool calls by tool and outcome (1h)",
        "bargauge",
        "sum by (tool, outcome) (increase(ecg_tool_calls_total[1h]))",
        "short",
    ),
    (
        "Tool p95 latency",
        "timeseries",
        "histogram_quantile(0.95, sum by (le, tool) (rate(ecg_tool_latency_seconds_bucket[5m])))",
        "s",
    ),
    (
        "Policy decisions by outcome (1h)",
        "piechart",
        "sum by (outcome) (increase(ecg_policy_decisions_total[1h]))",
        "short",
    ),
    (
        "Policy reason codes (1h)",
        "bargauge",
        "sum by (reason) (increase(ecg_policy_denial_reasons_total[1h]))",
        "short",
    ),
    (
        "Bounded tool retries (1h)",
        "timeseries",
        "sum by (tool) (increase(ecg_agent_retries_total[1h]))",
        "short",
    ),
    (
        "LLM tokens by provider and kind (1h)",
        "timeseries",
        "sum by (provider, kind) (increase(ecg_llm_tokens_total[1h]))",
        "short",
    ),
]


def panel(index: int, title: str, kind: str, expr: str, unit: str) -> dict[str, Any]:
    if kind != "timeseries":
        # increase() extrapolates; counts in gauges and pies read better as integers.
        expr = f"round({expr})"
    return {
        "id": index + 1,
        "title": title,
        "type": kind,
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "gridPos": {"h": 8, "w": 12, "x": (index % 2) * 12, "y": (index // 2) * 8},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
        "targets": [{"refId": "A", "expr": expr, "legendFormat": "__auto"}],
    }


def main() -> None:
    dashboard = {
        "uid": "ecg-overview",
        "title": "Enterprise Context Graph Agent",
        "tags": ["enterprise-context", "agent", "policy"],
        "timezone": "browser",
        "schemaVersion": 39,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "panels": [panel(index, *spec) for index, spec in enumerate(PANELS)],
    }
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(PANELS)} panels to {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
