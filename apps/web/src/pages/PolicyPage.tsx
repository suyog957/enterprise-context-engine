import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import type { AllowedActionsResponse } from "../api/types";
import { Badge, DataTable, ErrorNotice, Panel } from "../components/ui";
import { useSession } from "../state/session";

interface SearchHit {
  document_id: string;
  title: string;
  content: string;
}

export function PolicyPage() {
  const { request } = useSession();
  const [requisitionId, setRequisitionId] = useState("PR-1011");
  const [decisions, setDecisions] = useState<AllowedActionsResponse | null>(null);
  const [documents, setDocuments] = useState<SearchHit[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function evaluate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await request<AllowedActionsResponse>(
        `/requisitions/${requisitionId.trim().toUpperCase()}/allowed-actions`,
      );
      setDecisions(result);
      const cited = new Set(result.actions.flatMap((a) => a.policy_rules.map((r) => r.document_id)));
      const explanations = result.actions.flatMap((a) => a.explanations).join(" ") || "procurement policy";
      const hits = await request<{ hits: SearchHit[] }>("/search", {
        method: "POST",
        body: { query: explanations.slice(0, 500), document_types: ["PROCUREMENT_POLICY"], limit: 10 },
      });
      setDocuments(hits.hits.filter((hit) => cited.has(hit.document_id)));
    } catch (caught) {
      setDecisions(null);
      setError(caught instanceof ApiError ? caught.detail : "Policy evaluation failed");
    }
  }

  return (
    <div className="page">
      <section className="hero compact">
        <p className="eyebrow mono">Open Policy Agent</p>
        <h1>Policy decision viewer</h1>
        <p className="intro">
          Every candidate action is evaluated by the same Rego bundle that guards execution.
          Reason codes map to citable rules and policy documents.
        </p>
      </section>
      <form className="ask compact" onSubmit={evaluate}>
        <label htmlFor="policy-requisition">Requisition</label>
        <input
          id="policy-requisition"
          value={requisitionId}
          onChange={(event) => setRequisitionId(event.target.value)}
        />
        <button className="button" type="submit">
          Evaluate
        </button>
      </form>
      <ErrorNotice error={error} />
      {decisions && (
        <div className="grid">
          <Panel className="span-2" title={`Decisions for ${decisions.requisition_id}`} kicker={`policy ${decisions.policy_version}`}>
            <DataTable
              rows={decisions.actions.map((action) => ({
                action: action.action,
                status: action.status,
                reasons: action.reason_codes.join(", ") || "-",
                rules: action.policy_rules.map((rule) => rule.rule_id).join(", ") || "-",
                executable: action.executable ? "yes" : "discover only",
              }))}
            />
            <ul className="plain">
              {decisions.actions
                .filter((action) => action.explanations.length)
                .map((action) => (
                  <li key={action.action} className="small">
                    <Badge value={action.status} /> <strong>{action.action}</strong>:{" "}
                    {action.explanations.join(" ")}
                  </li>
                ))}
            </ul>
          </Panel>
          <Panel className="span-2" title="Cited policy documents" kicker="retrieved by rule reference">
            {documents.length ? (
              <ul className="plain">
                {documents.map((document) => (
                  <li key={document.document_id}>
                    <strong>{document.title}</strong> <span className="mono small">{document.document_id}</span>
                    <p className="small muted">{document.content}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="small muted">No rules are triggered, so no documents are cited.</p>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
