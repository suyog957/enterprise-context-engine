import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import type { DataQualityReport, EvaluationReport } from "../api/types";
import { Badge, DataTable, Empty, ErrorNotice, KeyValues, Panel } from "../components/ui";
import { useSession } from "../state/session";

type Metrics = Record<string, Record<string, unknown>>;

function number(value: unknown): string {
  return typeof value === "number" ? String(Math.round(value * 10000) / 10000) : "-";
}

export function EvaluationPage() {
  const { request, principalId } = useSession();
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [quality, setQuality] = useState<DataQualityReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    request<EvaluationReport>("/evaluation/latest")
      .then((value) => !cancelled && setReport(value))
      .catch((caught: unknown) => {
        if (cancelled) return;
        setReport(null);
        setError(caught instanceof ApiError ? caught.detail : "Evaluation report unavailable");
      });
    request<DataQualityReport>("/data-quality")
      .then((value) => !cancelled && setQuality(value))
      .catch(() => !cancelled && setQuality(null));
    return () => {
      cancelled = true;
    };
  }, [request, principalId]);

  const metrics = (report?.metrics ?? {}) as Metrics;
  const chat = metrics.chat as Record<string, unknown> | undefined;
  const retrieval = metrics.retrieval as { providers?: Record<string, Record<string, Record<string, unknown>>> } | undefined;
  const sparql = metrics.sparql;

  return (
    <div className="page">
      <section className="hero compact">
        <p className="eyebrow mono">Quality gates</p>
        <h1>Evaluation and data quality</h1>
        <p className="intro">
          Golden cases are scored on the whole trajectory, not only the final text. Critical
          gates (policy violations, unauthorized exposure, action validity) fail CI.
        </p>
      </section>
      <ErrorNotice error={error} />
      <div className="grid">
        {report?.gates && (
          <Panel className="span-2" title="Gates" kicker={`suite ${report.metadata?.suite ?? "-"}`}>
            <ul className="gates">
              {report.gates.map((gate) => (
                <li key={gate.gate}>
                  <Badge value={gate.passed ? "PASS" : "FAIL"} />
                  <span className="mono small">
                    {gate.gate}: {gate.actual} {gate.comparison} {gate.threshold}
                  </span>
                  {gate.critical && <span className="small muted">critical</span>}
                </li>
              ))}
            </ul>
            {report.metadata && (
              <KeyValues
                items={Object.entries(report.metadata).map(([key, value]) => [key.replaceAll("_", " "), String(value)])}
              />
            )}
          </Panel>
        )}
        {chat && (
          <Panel title="Agent trajectories" kicker={`${chat.evaluated as number} golden chat cases`}>
            <KeyValues
              items={[
                ["Task completion", number(chat.task_completion_rate)],
                ["Intent accuracy", number(chat.intent_accuracy)],
                ["Tool selection", number(chat.tool_selection_accuracy)],
                ["Tool arguments", number(chat.tool_argument_accuracy)],
                ["Action validity", number(chat.action_validity)],
                ["Policy violations", String(chat.policy_violations)],
                ["Unauthorized exposures", String(chat.unauthorized_exposures)],
                ["Groundedness", number(chat.groundedness)],
                ["Escalation rate", number(chat.escalation_rate)],
                ["Latency p50 / p95", `${number(chat.latency_p50_ms)} / ${number(chat.latency_p95_ms)} ms`],
              ]}
            />
            <DataTable
              rows={Object.entries((chat.by_category ?? {}) as Record<string, { cases: number; passed: number }>).map(
                ([category, value]) => ({ category, passed: `${value.passed}/${value.cases}` }),
              )}
            />
          </Panel>
        )}
        {sparql && (
          <Panel title="SPARQL" kicker="compared by execution result">
            <KeyValues
              items={[
                ["Execution success", number(sparql.execution_success_rate)],
                ["Result accuracy", number(sparql.result_accuracy)],
                ["Unsafe query rejection", number(sparql.unsafe_query_rejection_rate)],
                ["Latency p50 / p95", `${number(sparql.latency_p50_ms)} / ${number(sparql.latency_p95_ms)} ms`],
              ]}
            />
          </Panel>
        )}
        {retrieval?.providers && (
          <Panel className="span-2" title="Retrieval by embedding model and ranker" kicker="Recall@10 · MRR · nDCG@10">
            <DataTable
              rows={Object.entries(retrieval.providers).flatMap(([provider, result]) =>
                (["lexical", "vector", "hybrid"] as const).map((mode) => ({
                  provider,
                  ranker: mode,
                  recall_at_10: number(result[mode]?.recall_at_10),
                  precision_at_5: number(result[mode]?.precision_at_5),
                  mrr: number(result[mode]?.mrr),
                  ndcg_at_10: number(result[mode]?.ndcg_at_10),
                  p50_ms: number(result[mode]?.latency_p50_ms),
                })),
              )}
            />
          </Panel>
        )}
        {!report && !error && <Empty>Loading the latest evaluation report.</Empty>}
        <Panel className="span-2" title="Data quality" kicker={`retained, not silently cleaned · scope ${quality?.scope ?? "-"}`}>
          {quality ? (
            <>
              <DataTable rows={quality.issues} />
              <h3>SHACL validation</h3>
              <KeyValues
                items={Object.entries(quality.graph_validation)
                  .filter(([key]) => key !== "quarantine_sample")
                  .map(([key, value]) => [key.replaceAll("_", " "), String(value)])}
              />
              <DataTable
                rows={((quality.graph_validation.quarantine_sample ?? []) as Array<Record<string, unknown>>).slice(0, 8)}
                emptyText="No quarantined graph records."
              />
            </>
          ) : (
            <Empty>Data-quality summary unavailable.</Empty>
          )}
        </Panel>
      </div>
    </div>
  );
}
