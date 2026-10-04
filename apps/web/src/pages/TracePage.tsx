import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import type { TraceResponse, TraceStep } from "../api/types";
import { Badge, DataTable, Empty, ErrorNotice, KeyValues, Panel, Trajectory } from "../components/ui";
import { useSession } from "../state/session";

interface AgentRun {
  request_id: string;
  question: string;
  intent: string;
  status: string;
  answer: string;
  trajectory: TraceStep[];
  tool_calls: Array<Record<string, unknown>>;
}

export function TracePage() {
  const { traceId } = useParams();
  const navigate = useNavigate();
  const { request, principal } = useSession();
  const [input, setInput] = useState(traceId ?? "");
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const globalReader = principal?.roles.some((role) => role === "ADMIN" || role === "AUDITOR") ?? false;

  useEffect(() => {
    if (!traceId) return;
    let cancelled = false;
    setError(null);
    setTrace(null);
    setRun(null);
    const id = encodeURIComponent(traceId);
    // Owners can read their own trajectory; full traces (spans, audit) need ADMIN/AUDITOR.
    request<AgentRun>(`/agent/runs/${id}`)
      .then((value) => !cancelled && setRun(value))
      .catch(() => undefined);
    if (globalReader) {
      request<TraceResponse>(`/traces/${id}`)
        .then((value) => !cancelled && setTrace(value))
        .catch((caught: unknown) => {
          if (!cancelled) setError(caught instanceof ApiError ? caught.detail : "Trace unavailable");
        });
    }
    return () => {
      cancelled = true;
    };
  }, [traceId, request, globalReader]);

  function open(event: FormEvent) {
    event.preventDefault();
    if (input.trim()) navigate(`/traces/${encodeURIComponent(input.trim())}`);
  }

  const spans = trace?.spans ?? [];
  const total = Math.max(1, ...spans.map((span) => span.start_offset_ms + span.duration_ms));

  return (
    <div className="page">
      <section className="hero compact">
        <p className="eyebrow mono">OpenTelemetry · Jaeger</p>
        <h1>Agent trace viewer</h1>
        <p className="intro">
          The stored trajectory shows what the agent decided at each node; the distributed trace
          shows every SQL, SPARQL, OpenSearch, OPA and tool span behind it.
        </p>
      </section>
      <form className="ask compact" onSubmit={open}>
        <label htmlFor="trace-id">Request or trace ID</label>
        <input id="trace-id" value={input} onChange={(event) => setInput(event.target.value)} />
        <button className="button" type="submit">
          Open
        </button>
      </form>
      <ErrorNotice error={error} />
      {!traceId && <Empty>Open a trace from a chat answer, or paste a request ID.</Empty>}
      <div className="grid">
        {run && (
          <>
            <Panel title="Agent run" kicker={run.request_id} actions={<Badge value={run.status} />}>
              <KeyValues items={[["Question", run.question], ["Intent", run.intent], ["Answer", run.answer]]} />
            </Panel>
            <Panel title="Trajectory" kicker="LangGraph nodes">
              <Trajectory steps={run.trajectory} />
            </Panel>
          </>
        )}
        {traceId && !globalReader && (
          <Panel className="span-2" title="Distributed trace" kicker="restricted">
            <Empty>Span and audit details are available to ADMIN and AUDITOR principals.</Empty>
          </Panel>
        )}
        {trace && (
          <>
            <Panel
              className="span-2"
              title="Spans"
              kicker={`${spans.length} spans from ${trace.span_source}`}
              actions={
                trace.jaeger_url ? (
                  <a className="button ghost" href={trace.jaeger_url} target="_blank" rel="noreferrer">
                    Open in Jaeger
                  </a>
                ) : undefined
              }
            >
              {spans.length ? (
                <ul className="waterfall">
                  {spans.map((span) => (
                    <li key={span.span_id}>
                      <span className="mono small span-name">{span.operation}</span>
                      <span className="span-track">
                        <span
                          className="span-bar"
                          style={{
                            left: `${(span.start_offset_ms / total) * 100}%`,
                            width: `${Math.max(0.6, (span.duration_ms / total) * 100)}%`,
                          }}
                        />
                      </span>
                      <span className="mono small">{span.duration_ms.toFixed(1)} ms</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>No spans found (tracing disabled or not yet exported).</Empty>
              )}
            </Panel>
            <Panel className="span-2" title="Audit events" kicker="append-only">
              <DataTable
                rows={trace.events.map((event) => ({
                  action: event.action_type,
                  resource: event.resource_id,
                  outcome: event.outcome,
                  actor: event.actor_id,
                  at: event.occurred_at,
                }))}
                emptyText="No audit events for this request (read-only requests are not audited)."
              />
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}
