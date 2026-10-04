import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import type { AgentResponse } from "../api/types";
import { ProposalPanel } from "../components/ProposalPanel";
import {
  ActionTable,
  Badge,
  DataTable,
  Empty,
  ErrorNotice,
  Panel,
  Spinner,
  Trajectory,
} from "../components/ui";
import { useSession } from "../state/session";

const DEMO_QUESTIONS = [
  "Can PR-1007 be converted to a purchase order?",
  "Why can't PR-1011 become a purchase order?",
  "Show all purchases involving Acme.",
  "Which suppliers for Cloud Services have active contracts?",
  "What is our policy for high-risk suppliers?",
  "How much did Alice spend with Acme last year?",
  "Create a PO for PR-1012.",
];

export function ChatPage() {
  const { request } = useSession();
  const [question, setQuestion] = useState(DEMO_QUESTIONS[0]);
  const [response, setResponse] = useState<AgentResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(text: string) {
    setBusy(true);
    setError(null);
    try {
      setResponse(await request<AgentResponse>("/chat", { method: "POST", body: { question: text } }));
    } catch (caught) {
      setResponse(null);
      setError(caught instanceof ApiError ? caught.detail : "The agent request failed");
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (question.trim()) void ask(question.trim());
  }

  return (
    <div className="page">
      <section className="hero">
        <p className="eyebrow mono">Context engine · policy-grounded agent</p>
        <h1>Ask about procurement</h1>
        <p className="intro">
          Answers are assembled from authorized SQL, graph and document context; every action is
          checked by OPA before it is offered, and nothing is written without your confirmation.
        </p>
      </section>

      <form className="ask" onSubmit={submit}>
        <label htmlFor="question" className="sr-only">
          Question
        </label>
        <input
          id="question"
          value={question}
          maxLength={2000}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about a requisition, supplier, spend or policy"
        />
        <button className="button" type="submit" disabled={busy}>
          Ask
        </button>
      </form>
      <div className="chips">
        {DEMO_QUESTIONS.map((demo) => (
          <button
            key={demo}
            type="button"
            className="chip"
            onClick={() => {
              setQuestion(demo);
              void ask(demo);
            }}
          >
            {demo}
          </button>
        ))}
      </div>

      {busy && <Spinner label="Running the agent workflow" />}
      <ErrorNotice error={error} />
      {response && <AnswerView response={response} onRefresh={() => void ask(response.context.question)} />}
    </div>
  );
}

function AnswerView({ response, onRefresh }: { response: AgentResponse; onRefresh: () => void }) {
  const { context } = response;
  return (
    <div className="grid">
      <Panel
        className="span-2"
        kicker={`${response.intent.replaceAll("_", " ")} · ${context.routes.join(" + ") || "no data route"}`}
        title="Answer"
        actions={
          <div className="badges">
            <Badge value={response.status} />
            <Badge value={response.confidence} label={`confidence ${response.confidence}`} />
          </div>
        }
      >
        <p className="answer" data-testid="answer">
          {response.answer}
        </p>
        {response.citations.length > 0 && (
          <ul className="citations">
            {response.citations.map((citation) => (
              <li key={`${citation.kind}-${citation.ref}`} className="mono small">
                [{citation.kind}] {citation.ref} - {citation.label}
              </li>
            ))}
          </ul>
        )}
        {response.warnings.length > 0 && (
          <p className="small muted">Warnings: {response.warnings.join(", ")}</p>
        )}
        <p className="small muted">
          Request <code>{response.request_id}</code> ·{" "}
          <Link to={`/traces/${encodeURIComponent(response.request_id)}`}>open trace</Link>
          {response.requisition_id && (
            <>
              {" · "}
              <Link to={`/requisitions/${response.requisition_id}`}>requisition details</Link>
            </>
          )}
        </p>
      </Panel>

      {response.proposed_action && response.requisition_id && (
        <Panel className="span-2" title="Human confirmation" kicker="write-protected action">
          <ProposalPanel
            requisitionId={response.requisition_id}
            status={response.proposed_action.status}
            onChanged={onRefresh}
          />
        </Panel>
      )}

      <Panel title="Entities resolved" kicker="entity resolution">
        {context.entities.length ? (
          <ul className="plain">
            {context.entities.map((entity) => (
              <li key={`${entity.entity_type}-${entity.entity_id}`}>
                <strong>{entity.label}</strong>{" "}
                <span className="muted small">
                  {entity.entity_type}
                  {entity.match_band ? ` · ${entity.match_band} ${(entity.confidence * 100).toFixed(0)}%` : ""}
                  {entity.method ? ` · ${entity.method}` : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No entities were needed.</Empty>
        )}
      </Panel>

      <Panel title="Allowed actions" kicker={`OPA ${context.freshness.policy_version ?? ""}`}>
        <ActionTable actions={context.allowed_actions} />
      </Panel>

      <Panel title="Retrieved facts" kicker="authorized context with sources">
        <DataTable
          rows={context.facts.map((fact) => ({
            fact: fact.predicate,
            value: fact.value,
            source: fact.source_system ?? fact.source,
          }))}
          emptyText="No facts for this intent."
        />
        {context.records.length > 0 && (
          <>
            <h3>Records</h3>
            <DataTable rows={context.records.slice(0, 15)} />
          </>
        )}
      </Panel>

      <Panel title="Supporting documents" kicker="hybrid retrieval · untrusted evidence">
        {context.documents.length ? (
          <ul className="plain">
            {context.documents.map((document) => (
              <li key={document.document_id}>
                <strong>{document.title}</strong>{" "}
                <span className="mono small">
                  {document.document_id} · RRF #{document.rrf_rank} (BM25 {document.bm25_rank ?? "-"},
                  vector {document.vector_rank ?? "-"})
                </span>
                {document.flagged_instructions ? (
                  <p className="small notice">
                    Contains instruction-like text; treated as untrusted and not used.
                  </p>
                ) : (
                  <p className="small muted">{document.snippet}</p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No documents retrieved.</Empty>
        )}
      </Panel>

      <Panel title="Agent steps" kicker="LangGraph trajectory">
        <Trajectory steps={response.trajectory} />
      </Panel>

      <Panel title="Tool calls" kicker="allowlisted, typed, server-side">
        <DataTable
          rows={response.tool_calls.map((call) => ({
            tool: call.tool,
            status: call.status,
            ms: call.duration_ms,
            attempts: call.attempts,
            arguments: call.arguments,
          }))}
        />
        <p className="small muted">
          Freshness: {context.freshness.graph_projection ?? "graph projection unknown"}
        </p>
      </Panel>
    </div>
  );
}
