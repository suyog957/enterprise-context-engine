"""Persistence of agent trajectories (best effort: a storage failure never fails a reply)."""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from enterprise_context.agents.workflow import AgentResponse

logger = logging.getLogger(__name__)


class AgentRunRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def save(self, response: AgentResponse, principal_id: str, question: str) -> None:
        tool_calls = [
            {
                "tool": call.tool,
                "arguments": call.arguments,
                "status": call.status.value,
                "duration_ms": call.duration_ms,
                "attempts": call.attempts,
                "error": call.error,
            }
            for call in response.tool_calls
        ]
        try:
            with psycopg.connect(self._database_url, connect_timeout=3) as connection:
                connection.execute(
                    """INSERT INTO agent_run
                       (request_id, trace_id, principal_id, question, intent, status,
                        confidence, answer, trajectory, tool_calls, plan, warnings, errors)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (request_id) DO NOTHING""",
                    (
                        response.request_id,
                        response.trace_id,
                        principal_id,
                        question,
                        response.intent.value,
                        response.status,
                        response.confidence.value,
                        response.answer,
                        Jsonb([step.model_dump(mode="json") for step in response.trajectory]),
                        Jsonb(tool_calls),
                        Jsonb(response.plan.model_dump(mode="json")) if response.plan else None,
                        Jsonb(response.warnings),
                        Jsonb(response.errors),
                    ),
                )
        except psycopg.Error:
            logger.warning(
                "Could not persist agent run", extra={"run_request_id": response.request_id}
            )

    def get(self, request_or_trace_id: str) -> dict[str, Any] | None:
        with psycopg.connect(
            self._database_url, connect_timeout=3, row_factory=dict_row
        ) as connection:
            row = connection.execute(
                """SELECT request_id, trace_id, principal_id, question, intent, status,
                          confidence, answer, trajectory, tool_calls, plan, warnings, errors,
                          created_at
                   FROM agent_run WHERE request_id = %s OR trace_id = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (request_or_trace_id, request_or_trace_id),
            ).fetchone()
        return dict(row) if row else None
