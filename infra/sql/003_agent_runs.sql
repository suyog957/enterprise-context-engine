-- Stored agent trajectories for trace inspection and trajectory evaluation.
CREATE TABLE IF NOT EXISTS agent_run (
    request_id TEXT PRIMARY KEY,
    trace_id TEXT,
    principal_id TEXT NOT NULL,
    question TEXT NOT NULL,
    intent TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    answer TEXT NOT NULL,
    trajectory JSONB NOT NULL,
    tool_calls JSONB NOT NULL,
    plan JSONB,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_run_principal_time
    ON agent_run(principal_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_run_trace ON agent_run(trace_id);
