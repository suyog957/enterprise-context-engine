import type { ReactNode } from "react";

import type { ActionAvailability, TraceStep } from "../api/types";

export function Panel({
  title,
  kicker,
  actions,
  children,
  className = "",
}: {
  title: string;
  kicker?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <header className="panel-header">
        <div>
          {kicker && <p className="kicker">{kicker}</p>}
          <h2>{title}</h2>
        </div>
        {actions}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

const TONES: Record<string, string> = {
  AVAILABLE: "good",
  ANSWERED: "good",
  HIGH: "good",
  OK: "good",
  completed: "good",
  PASS: "good",
  CONFIRMATION_REQUIRED: "good",
  SIMULATED: "good",
  CREATED: "good",
  APPROVED: "good",
  APPROVAL_REQUIRED: "warn",
  PARTIAL: "warn",
  REDUCED: "warn",
  NEEDS_CLARIFICATION: "warn",
  corrected: "warn",
  degraded: "warn",
  clarification: "warn",
  PENDING: "warn",
  BLOCKED: "bad",
  UNAVAILABLE: "bad",
  LOW: "bad",
  NOT_PERMITTED: "bad",
  DENIED: "bad",
  FAIL: "bad",
};

export function Badge({ value, label }: { value: string; label?: string }) {
  const tone = TONES[value] ?? "neutral";
  return (
    <span className={`badge badge-${tone}`} data-status={value}>
      {label ?? value.replaceAll("_", " ")}
    </span>
  );
}

export function ErrorNotice({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p className="notice notice-error" role="alert">
      {error}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function KeyValues({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="key-values">
      {items.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DataTable({
  rows,
  columns,
  emptyText = "No rows.",
}: {
  rows: Array<Record<string, unknown>>;
  columns?: string[];
  emptyText?: string;
}) {
  if (!rows.length) return <Empty>{emptyText}</Empty>;
  const keys = columns ?? Object.keys(rows[0]);
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {keys.map((key) => (
              <th key={key}>{key.replaceAll("_", " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {keys.map((key) => (
                <td key={key}>{formatCell(row[key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return shortUri(value);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function shortUri(value: string): string {
  if (!value.startsWith("http")) return value;
  return decodeURIComponent(value.split("#").pop()?.split("/").pop() ?? value);
}

export function ActionTable({ actions }: { actions: ActionAvailability[] }) {
  if (!actions.length) return <Empty>No policy decision is available.</Empty>;
  return (
    <ul className="action-list">
      {actions.map((action) => (
        <li key={action.action} className="action-item" data-action={action.action}>
          <div className="action-head">
            <strong>{action.label ?? action.action}</strong>
            <Badge value={action.status} />
            {!action.executable && <span className="muted small">discover only</span>}
          </div>
          {action.explanations.length > 0 && (
            <p className="small">{action.explanations.join(" ")}</p>
          )}
          {action.policy_rules.length > 0 && (
            <p className="small mono">
              {action.policy_rules.map((rule) => `${rule.rule_id} (${rule.document_id})`).join(", ")}
            </p>
          )}
          {action.preconditions.length > 0 && (
            <details>
              <summary className="small">Preconditions and effects</summary>
              <ul className="small">
                {action.preconditions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
                {action.effects.map((item) => (
                  <li key={item}>Effect: {item}</li>
                ))}
              </ul>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}

export function Trajectory({ steps }: { steps: TraceStep[] }) {
  if (!steps.length) return <Empty>No trajectory recorded.</Empty>;
  return (
    <ol className="trajectory">
      {steps.map((step, index) => (
        <li key={`${step.node}-${index}`} data-node={step.node}>
          <div className="step-head">
            <span className="mono">{step.node}</span>
            <Badge value={step.status} />
            <span className="muted small">{step.duration_ms.toFixed(1)} ms</span>
          </div>
          <p className="small">{step.detail}</p>
        </li>
      ))}
    </ol>
  );
}

export function Spinner({ label = "Working" }: { label?: string }) {
  return (
    <span className="spinner" role="status">
      {label}...
    </span>
  );
}
