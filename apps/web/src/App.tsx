import { useEffect, useRef, useState } from "react";
import cytoscape, { type ElementDefinition } from "cytoscape";
import {
  Activity,
  ArrowRight,
  BarChart3,
  Boxes,
  ClipboardCheck,
  Database,
  FileSearch,
  GitBranch,
  Network,
  Search,
  ShieldCheck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

type Health = {
  status: string;
  service: string;
};

type RequisitionContext = {
  requisition_id: string;
  buyer_id: string;
  buyer_name: string;
  business_unit_id: string;
  state: string;
  amount: string;
  currency: string;
  categories: string[];
  supplier: {
    canonical_entity_id: string | null;
    preferred_name: string | null;
    status: string;
    risk_rating: string;
  };
  active_contract_ids: string[];
  graph_available: boolean;
  graph_facts: Array<{ predicate: string; object_value: string }>;
};

type ActionResult = {
  requisition_id: string;
  available_actions: string[];
  policy_version: string;
  actions: Array<{
    action: string;
    status: "AVAILABLE" | "APPROVAL_REQUIRED" | "BLOCKED";
    reason_codes: string[];
    explanations: string[];
  }>;
};

type SearchHit = {
  document_id: string;
  title: string;
  content: string;
  document_type: string;
  source_system: string;
  source_record_id: string;
  bm25_rank: number | null;
  vector_rank: number | null;
  rrf_rank: number;
};

type AgentChatResponse = {
  answer: string;
  requisition_id: string;
  context: RequisitionContext;
  policy_decision: {
    allowed: boolean;
    approval_required: boolean;
    reason_codes: string[];
    explanations: string[];
    policy_version: string;
  };
  available_actions: string[];
  supporting_documents: SearchHit[];
  context_warnings: string[];
  trajectory: Array<{ node: string; status: string; detail: string }>;
};

type SimulationResponse = {
  available: boolean;
  approval_required: boolean;
  dry_run: boolean;
  decision: {
    reason_codes: string[];
    explanations: string[];
  };
};

type EntityCandidate = {
  canonical_entity_id: string;
  entity_type: string;
  preferred_name: string;
  status: string;
  risk_rating: string;
  matched_alias: string | null;
  source_system: string | null;
  confidence_score: number | null;
};

type EntityContext = {
  entity: EntityCandidate;
  aliases: Array<{
    source_system: string;
    source_supplier_id: string;
    source_record_id: string;
    alias: string;
    resolution_method: string;
    confidence_score: number;
    review_required: boolean;
  }>;
  related_requisition_ids: string[];
  related_purchase_order_ids: string[];
  active_contract_ids: string[];
  provenance: Array<{
    source_system: string;
    source_record_id: string;
    source_supplier_id: string;
    fact: string;
  }>;
};

type EntityResolveResponse = {
  query: string;
  candidates: EntityCandidate[];
};

type GraphQueryResponse = {
  query_type: string;
  rows: Array<Record<string, string>>;
  boolean: boolean | null;
};

type SearchResponse = {
  query: string;
  hits: SearchHit[];
};

type EvaluationReport = {
  dataset_seed?: number;
  policy_backend?: string;
  policy_version?: string;
  metrics?: Record<string, number>;
  mismatches?: Array<Record<string, unknown>>;
};

type TraceResponse = {
  trace_id: string;
  events: Array<Record<string, unknown>>;
};

type ViewKey =
  | "context"
  | "entities"
  | "graph"
  | "policy"
  | "search"
  | "trace"
  | "evaluation";

const modules: Array<{ label: string; view: ViewKey; icon: LucideIcon }> = [
  { label: "Context workspace", view: "context", icon: Network },
  { label: "Entity explorer", view: "entities", icon: GitBranch },
  { label: "Graph explorer", view: "graph", icon: Boxes },
  { label: "Policy decisions", view: "policy", icon: ShieldCheck },
  { label: "Document retrieval", view: "search", icon: FileSearch },
  { label: "Agent trace", view: "trace", icon: ClipboardCheck },
  { label: "Evaluation", view: "evaluation", icon: BarChart3 },
];

function principalHeaders(principalId: string): Record<string, string> {
  return { "X-Dev-Principal": principalId };
}

async function readJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = "Request failed.";
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail.replaceAll("_", " ");
    } catch {
      message = response.statusText || message;
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function formatMoney(amount: string, currency: string) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
  }).format(Number(amount));
}

export default function App() {
  const graphContainerRef = useRef<HTMLDivElement | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [apiUnavailable, setApiUnavailable] = useState(false);
  const [activeView, setActiveView] = useState<ViewKey>("context");
  const [requisitionId, setRequisitionId] = useState("PR-1007");
  const [principalId, setPrincipalId] = useState("user-alice");
  const [context, setContext] = useState<RequisitionContext | null>(null);
  const [actionResult, setActionResult] = useState<ActionResult | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [agentSteps, setAgentSteps] = useState<AgentChatResponse["trajectory"]>([]);
  const [supportingDocuments, setSupportingDocuments] = useState<SearchHit[]>([]);
  const [contextWarnings, setContextWarnings] = useState<string[]>([]);
  const [simulationStatus, setSimulationStatus] = useState<string | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [entityQuery, setEntityQuery] = useState("Acme");
  const [entityCandidates, setEntityCandidates] = useState<EntityCandidate[]>([]);
  const [entityDetail, setEntityDetail] = useState<EntityContext | null>(null);
  const [entityError, setEntityError] = useState<string | null>(null);

  const [graphQuery, setGraphQuery] = useState("SELECT ?s WHERE { ?s ?p ?o } LIMIT 5");
  const [graphResult, setGraphResult] = useState<GraphQueryResponse | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);

  const [documentQuery, setDocumentQuery] = useState("blocked supplier policy");
  const [documentHits, setDocumentHits] = useState<SearchHit[]>([]);
  const [documentError, setDocumentError] = useState<string | null>(null);

  const [traceId, setTraceId] = useState("");
  const [traceResult, setTraceResult] = useState<TraceResponse | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);

  const [evaluationReport, setEvaluationReport] = useState<EvaluationReport | null>(null);
  const [evaluationError, setEvaluationError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/health/ready", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("API is not ready");
        return response.json() as Promise<Health>;
      })
      .then(setHealth)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setApiUnavailable(true);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (activeView !== "graph" || !graphContainerRef.current) return;
    const elements: ElementDefinition[] = [];
    if (context) {
      elements.push(
        { data: { id: context.requisition_id, label: context.requisition_id } },
        { data: { id: "buyer", label: context.buyer_name } },
        { data: { id: "supplier", label: context.supplier.preferred_name ?? "Unresolved" } },
        { data: { id: "state", label: context.state } },
        {
          data: {
            id: "buyer-edge",
            source: "buyer",
            target: context.requisition_id,
            label: "OWNS",
          },
        },
        {
          data: {
            id: "supplier-edge",
            source: context.requisition_id,
            target: "supplier",
            label: "HAS_SUPPLIER",
          },
        },
        {
          data: {
            id: "state-edge",
            source: context.requisition_id,
            target: "state",
            label: "HAS_STATE",
          },
        },
      );
      context.categories.slice(0, 4).forEach((category, index) => {
        const id = `category-${index}`;
        elements.push(
          { data: { id, label: category } },
          {
            data: {
              id: `category-edge-${index}`,
              source: context.requisition_id,
              target: id,
              label: "CATEGORY",
            },
          },
        );
      });
    } else if (graphResult?.rows.length) {
      graphResult.rows.slice(0, 6).forEach((row, rowIndex) => {
        const rootId = `row-${rowIndex}`;
        elements.push({ data: { id: rootId, label: `Row ${rowIndex + 1}` } });
        Object.entries(row).forEach(([key, value], valueIndex) => {
          const valueId = `${rootId}-${valueIndex}`;
          elements.push(
            { data: { id: valueId, label: value.split(/[\/#]/).at(-1) ?? value } },
            { data: { id: `${valueId}-edge`, source: rootId, target: valueId, label: key } },
          );
        });
      });
    } else {
      elements.push({ data: { id: "empty", label: "Load context or run SPARQL" } });
    }

    const cy = cytoscape({
      container: graphContainerRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#1c765f",
            color: "#172622",
            label: "data(label)",
            "font-size": "9px",
            "min-zoomed-font-size": "8px",
            "text-margin-y": -8,
            "text-wrap": "wrap",
            "text-max-width": "90px",
            width: "34px",
            height: "34px",
          },
        },
        {
          selector: "edge",
          style: {
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "line-color": "#a8bdb4",
            "target-arrow-color": "#a8bdb4",
            label: "data(label)",
            "font-size": "7px",
            color: "#64736d",
          },
        },
      ],
      layout: { name: "breadthfirst", directed: true, padding: 26, spacingFactor: 1.2 },
      userZoomingEnabled: true,
      userPanningEnabled: true,
    });
    return () => cy.destroy();
  }, [activeView, context, graphResult]);

  async function inspectRequisition(event?: React.FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setLoading(true);
    setLookupError(null);
    setContext(null);
    setActionResult(null);
    setAnswer(null);
    setAgentSteps([]);
    setSupportingDocuments([]);
    setContextWarnings([]);
    try {
      const result = await readJson<AgentChatResponse>("/chat", {
        method: "POST",
        headers: { ...principalHeaders(principalId), "Content-Type": "application/json" },
        body: JSON.stringify({
          question: `Can ${requisitionId.trim()} be converted into a purchase order?`,
        }),
      });
      setContext(result.context);
      setAnswer(result.answer);
      setAgentSteps(result.trajectory);
      setSupportingDocuments(result.supporting_documents);
      setContextWarnings(result.context_warnings);
      setActionResult({
        requisition_id: result.requisition_id,
        available_actions: result.available_actions,
        policy_version: result.policy_decision.policy_version,
        actions: [{
          action: "CREATE_PURCHASE_ORDER",
          status: result.policy_decision.allowed
            ? "AVAILABLE"
            : result.policy_decision.approval_required
              ? "APPROVAL_REQUIRED"
              : "BLOCKED",
          reason_codes: result.policy_decision.reason_codes,
          explanations: result.policy_decision.explanations,
        }],
      });
    } catch (error) {
      setLookupError(error instanceof Error ? error.message : "Request failed.");
    } finally {
      setLoading(false);
    }
  }

  async function refreshPolicyDecision(event?: React.FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setLookupError(null);
    try {
      const result = await readJson<ActionResult>(
        `/requisitions/${encodeURIComponent(requisitionId)}/allowed-actions`,
        { headers: principalHeaders(principalId) },
      );
      setActionResult(result);
    } catch (error) {
      setLookupError(error instanceof Error ? error.message : "Policy lookup failed.");
    }
  }

  async function simulatePurchaseOrder() {
    if (!actionResult?.available_actions.includes("CREATE_PURCHASE_ORDER")) return;
    setSimulating(true);
    setSimulationStatus(null);
    try {
      const result = await readJson<SimulationResponse>(
        `/requisitions/${encodeURIComponent(actionResult.requisition_id)}/simulate-po`,
        { method: "POST", headers: principalHeaders(principalId) },
      );
      setSimulationStatus(
        result.available
          ? result.dry_run
            ? "Simulation passed. No purchase order was written."
            : "Policy checks passed."
          : result.decision.explanations.join(" ") || "Policy blocked this action.",
      );
    } catch (error) {
      setSimulationStatus(error instanceof Error ? error.message : "Simulation failed.");
    } finally {
      setSimulating(false);
    }
  }

  async function resolveEntity(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setEntityError(null);
    setEntityDetail(null);
    try {
      const result = await readJson<EntityResolveResponse>("/entities/resolve", {
        method: "POST",
        headers: { ...principalHeaders(principalId), "Content-Type": "application/json" },
        body: JSON.stringify({ query: entityQuery, limit: 8 }),
      });
      setEntityCandidates(result.candidates);
    } catch (error) {
      setEntityError(error instanceof Error ? error.message : "Entity lookup failed.");
    }
  }

  async function loadEntityContext(entityId: string) {
    setEntityError(null);
    try {
      const result = await readJson<EntityContext>(
        `/entities/${encodeURIComponent(entityId)}/context`,
        { headers: principalHeaders(principalId) },
      );
      setEntityDetail(result);
    } catch (error) {
      setEntityError(error instanceof Error ? error.message : "Entity context failed.");
    }
  }

  async function runGraphQuery(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setGraphError(null);
    try {
      const result = await readJson<GraphQueryResponse>("/graph/query", {
        method: "POST",
        headers: { ...principalHeaders(principalId), "Content-Type": "application/json" },
        body: JSON.stringify({ query: graphQuery }),
      });
      setGraphResult(result);
    } catch (error) {
      setGraphError(error instanceof Error ? error.message : "Graph query failed.");
    }
  }

  async function searchDocuments(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setDocumentError(null);
    try {
      const result = await readJson<SearchResponse>("/search", {
        method: "POST",
        headers: { ...principalHeaders(principalId), "Content-Type": "application/json" },
        body: JSON.stringify({ query: documentQuery, limit: 8 }),
      });
      setDocumentHits(result.hits);
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "Document search failed.");
    }
  }

  async function loadTrace(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTraceError(null);
    setTraceResult(null);
    try {
      const result = await readJson<TraceResponse>(
        `/traces/${encodeURIComponent(traceId.trim())}`,
        { headers: principalHeaders(principalId) },
      );
      setTraceResult(result);
    } catch (error) {
      setTraceError(error instanceof Error ? error.message : "Trace lookup failed.");
    }
  }

  async function loadEvaluation() {
    setEvaluationError(null);
    try {
      const result = await readJson<EvaluationReport>("/evaluation/latest", {
        headers: principalHeaders(principalId),
      });
      setEvaluationReport(result);
    } catch (error) {
      setEvaluationError(error instanceof Error ? error.message : "Evaluation report unavailable.");
    }
  }

  const action = actionResult?.actions[0];
  const amount = context ? formatMoney(context.amount, context.currency) : null;

  return (
    <main className="app-shell">
      <aside className="rail" aria-label="Primary navigation">
        <button
          className="brand-mark"
          type="button"
          aria-label="Context Engine home"
          onClick={() => setActiveView("context")}
        >
          <Network size={21} strokeWidth={2.2} />
        </button>
        <nav className="rail-nav">
          {modules.map(({ label, icon: Icon, view }) => (
            <button
              className={`nav-item${activeView === view ? " is-active" : ""}`}
              key={view}
              type="button"
              aria-label={label}
              title={label}
              aria-current={activeView === view ? "page" : undefined}
              onClick={() => setActiveView(view)}
            >
              <Icon size={19} strokeWidth={1.8} />
            </button>
          ))}
        </nav>
        <button className="nav-item rail-bottom" type="button" aria-label="Service status" title="Service status">
          <Activity size={19} strokeWidth={1.8} />
        </button>
      </aside>

      <section className="workspace" id="workspace">
        <header className="topbar">
          <div className="wordmark"><span className="wordmark-dot" /> CONTEXT ENGINE</div>
          <div className="topbar-meta">
            <label className="principal-inline">
              <span>Principal</span>
              <select value={principalId} onChange={(event) => setPrincipalId(event.target.value)}>
                <option value="user-alice">Alice</option>
                <option value="user-buyer-010">Manager</option>
                <option value="user-auditor">Auditor</option>
                <option value="user-admin">Admin</option>
              </select>
            </label>
            <span className={`api-status${health ? " is-ready" : ""}`}>
              <span className="status-dot" />
              {health ? "API connected" : apiUnavailable ? "API unavailable" : "Checking API"}
            </span>
          </div>
        </header>

        <div className="content">
          <div className="eyebrow"><Database size={14} /> PLATFORM WORKSPACE</div>
          <h1>Enterprise context,<br />made inspectable.</h1>
          <p className="intro">
            Inspect requisitions, supplier identity, graph relationships, retrieval evidence,
            policy decisions, traces, and evaluation output from the local synthetic platform.
          </p>

          {activeView === "context" && (
            <section className="foundation" aria-labelledby="foundation-title">
              <div className="section-heading">
                <div>
                  <p className="section-kicker">PROCUREMENT / ACTION ELIGIBILITY</p>
                  <h2 id="foundation-title">Can this requisition become a PO?</h2>
                </div>
                <span className="phase-badge">POLICY-GROUNDED</span>
              </div>
              <form className="lookup-form" onSubmit={inspectRequisition}>
                <label className="field-label" htmlFor="requisition-id">Requisition ID</label>
                <div className="lookup-controls compact-controls">
                  <input
                    id="requisition-id"
                    value={requisitionId}
                    onChange={(event) => setRequisitionId(event.target.value)}
                    placeholder="PR-1007"
                    required
                  />
                  <button className="inspect-button" type="submit" disabled={loading || !health}>
                    <Search size={16} />
                    {loading ? "Checking" : "Check eligibility"}
                  </button>
                </div>
              </form>

              {lookupError && <div className="lookup-error" role="alert">{lookupError}</div>}

              {context && (
                <div className="decision-layout" aria-live="polite">
                  <section className="context-summary" aria-label="Requisition context">
                    <div className="context-title-row">
                      <div>
                        <span className="context-label">PURCHASE REQUISITION</span>
                        <h3>{context.requisition_id}</h3>
                      </div>
                      <span className="state-chip">{context.state}</span>
                    </div>
                    <dl className="fact-grid">
                      <div><dt>Supplier</dt><dd>{context.supplier.preferred_name ?? "Unresolved"}</dd></div>
                      <div><dt>Supplier status</dt><dd>{context.supplier.status}</dd></div>
                      <div><dt>Risk</dt><dd>{context.supplier.risk_rating}</dd></div>
                      <div><dt>Buyer</dt><dd>{context.buyer_name}</dd></div>
                      <div><dt>Amount</dt><dd>{amount}</dd></div>
                      <div><dt>Business unit</dt><dd>{context.business_unit_id}</dd></div>
                    </dl>
                    <div className="category-line">
                      <span>CATEGORIES</span>
                      <strong>{context.categories.join(", ") || "Not classified"}</strong>
                    </div>
                    {context.graph_facts.length > 0 && (
                      <div className="graph-facts">
                        <span className="context-label">CONTEXT GRAPH FACTS</span>
                        <ul>
                          {context.graph_facts.slice(0, 6).map((fact, index) => (
                            <li key={`${fact.predicate}-${index}`}>
                              <span>{fact.predicate}</span>{fact.object_value}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {contextWarnings.length > 0 && (
                      <p className="context-warning" role="status">
                        Reduced context: {contextWarnings.join(", ").replaceAll("_", " ").toLowerCase()}.
                      </p>
                    )}
                  </section>

                  {action && (
                    <section className={`action-result is-${action.status.toLowerCase()}`} aria-label="Policy decision">
                      <div className="action-result-top">
                        <span className="context-label">OPA DECISION / {actionResult?.policy_version}</span>
                        <span className="decision-status">{action.status.replaceAll("_", " ")}</span>
                      </div>
                      <h3>Create purchase order</h3>
                      {answer && <p className="agent-answer">{answer}</p>}
                      {action.explanations.length > 0 ? (
                        <ul className="reason-list">
                          {action.explanations.map((explanation, index) => (
                            <li key={`${action.reason_codes[index]}-${index}`}>
                              <span>{action.reason_codes[index]}</span>{explanation}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="decision-explanation">
                          All current policy preconditions are satisfied for this principal and requisition.
                        </p>
                      )}
                      <button
                        className="simulate-button"
                        type="button"
                        onClick={simulatePurchaseOrder}
                        disabled={simulating || !actionResult?.available_actions.includes("CREATE_PURCHASE_ORDER")}
                      >
                        {simulating ? "Simulating" : "Simulate action"} <ArrowRight size={15} />
                      </button>
                      {simulationStatus && <p className="simulation-status" role="status">{simulationStatus}</p>}
                    </section>
                  )}
                  <TracePanel agentSteps={agentSteps} />
                  <SourcePanel supportingDocuments={supportingDocuments} />
                </div>
              )}

              {!context && !lookupError && (
                <div className="foundation-note">
                  <span className="note-index">01</span>
                  <p>Ready for an authorized context lookup when the API and policy service are available.</p>
                </div>
              )}
            </section>
          )}

          {activeView === "entities" && (
            <section className="foundation">
              <PanelHeading kicker="ENTITY RESOLUTION" title="Canonical supplier explorer" />
              <form className="lookup-form" onSubmit={resolveEntity}>
                <label className="field-label" htmlFor="entity-query">Supplier name, alias, or ID</label>
                <div className="lookup-controls compact-controls">
                  <input
                    id="entity-query"
                    value={entityQuery}
                    onChange={(event) => setEntityQuery(event.target.value)}
                  />
                  <button className="inspect-button" type="submit" disabled={!health}>
                    <Search size={16} /> Resolve
                  </button>
                </div>
              </form>
              {entityError && <div className="lookup-error" role="alert">{entityError}</div>}
              <div className="two-column-body">
                <ResultList
                  items={entityCandidates}
                  emptyText="No entity candidates loaded."
                  renderItem={(candidate) => (
                    <button
                      className="result-row"
                      key={candidate.canonical_entity_id}
                      type="button"
                      onClick={() => loadEntityContext(candidate.canonical_entity_id)}
                    >
                      <strong>{candidate.preferred_name}</strong>
                      <span>{candidate.status} / {candidate.risk_rating}</span>
                      <small>{candidate.canonical_entity_id}</small>
                    </button>
                  )}
                />
                <div className="data-panel">
                  {entityDetail ? (
                    <>
                      <span className="context-label">ALIASES AND PROVENANCE</span>
                      <h3>{entityDetail.entity.preferred_name}</h3>
                      <ul className="detail-list">
                        {entityDetail.aliases.map((alias) => (
                          <li key={`${alias.source_system}-${alias.source_supplier_id}`}>
                            <strong>{alias.alias}</strong>
                            <span>{alias.source_system} / {alias.resolution_method} / {alias.confidence_score.toFixed(2)}</span>
                          </li>
                        ))}
                      </ul>
                      <div className="category-line">
                        <span>REQUISITIONS</span>
                        <strong>{entityDetail.related_requisition_ids.slice(0, 8).join(", ") || "None visible"}</strong>
                      </div>
                    </>
                  ) : (
                    <p className="empty-state">Select a candidate to inspect aliases, provenance, and related documents.</p>
                  )}
                </div>
              </div>
            </section>
          )}

          {activeView === "graph" && (
            <section className="foundation">
              <PanelHeading kicker="CONTEXT GRAPH" title="Explore RDF projection" />
              <div className="graph-canvas" ref={graphContainerRef} aria-label="Context graph visualization" />
              <form className="lookup-form" onSubmit={runGraphQuery}>
                <label className="field-label" htmlFor="graph-query">Read-only SPARQL</label>
                <textarea
                  id="graph-query"
                  value={graphQuery}
                  onChange={(event) => setGraphQuery(event.target.value)}
                  rows={4}
                />
                <button className="inspect-button form-button" type="submit" disabled={!health}>
                  <Search size={16} /> Run query
                </button>
              </form>
              {graphError && <div className="lookup-error" role="alert">{graphError}</div>}
              {graphResult && <JsonBlock value={graphResult} />}
            </section>
          )}

          {activeView === "policy" && (
            <section className="foundation">
              <PanelHeading kicker="OPA DECISION VIEWER" title="Allowed action discovery" />
              <form className="lookup-form" onSubmit={refreshPolicyDecision}>
                <label className="field-label" htmlFor="policy-requisition-id">Requisition ID</label>
                <div className="lookup-controls compact-controls">
                  <input
                    id="policy-requisition-id"
                    value={requisitionId}
                    onChange={(event) => setRequisitionId(event.target.value)}
                  />
                  <button className="inspect-button" type="submit" disabled={!health}>
                    <ShieldCheck size={16} /> Evaluate
                  </button>
                </div>
              </form>
              {lookupError && <div className="lookup-error" role="alert">{lookupError}</div>}
              {action ? (
                <div className={`data-panel decision-panel is-${action.status.toLowerCase()}`}>
                  <span className="context-label">CREATE_PURCHASE_ORDER</span>
                  <h3>{action.status.replaceAll("_", " ")}</h3>
                  <ul className="reason-list">
                    {(action.explanations.length ? action.explanations : ["Action is allowed."]).map((item, index) => (
                      <li key={`${item}-${index}`}>
                        <span>{action.reason_codes[index] ?? "ALLOWED"}</span>{item}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="empty-state inside">No policy decision loaded yet.</p>
              )}
            </section>
          )}

          {activeView === "search" && (
            <section className="foundation">
              <PanelHeading kicker="HYBRID RETRIEVAL" title="Policy and contract search" />
              <form className="lookup-form" onSubmit={searchDocuments}>
                <label className="field-label" htmlFor="document-query">Search query</label>
                <div className="lookup-controls compact-controls">
                  <input
                    id="document-query"
                    value={documentQuery}
                    onChange={(event) => setDocumentQuery(event.target.value)}
                  />
                  <button className="inspect-button" type="submit" disabled={!health}>
                    <Search size={16} /> Search
                  </button>
                </div>
              </form>
              {documentError && <div className="lookup-error" role="alert">{documentError}</div>}
              <SourcePanel supportingDocuments={documentHits} />
            </section>
          )}

          {activeView === "trace" && (
            <section className="foundation">
              <PanelHeading kicker="OBSERVABILITY" title="Audit and trace lookup" />
              <form className="lookup-form" onSubmit={loadTrace}>
                <label className="field-label" htmlFor="trace-id">Request or event ID</label>
                <div className="lookup-controls compact-controls">
                  <input
                    id="trace-id"
                    value={traceId}
                    onChange={(event) => setTraceId(event.target.value)}
                    placeholder="Paste a request ID"
                    required
                  />
                  <button className="inspect-button" type="submit" disabled={!health}>
                    <ClipboardCheck size={16} /> Load
                  </button>
                </div>
              </form>
              {traceError && <div className="lookup-error" role="alert">{traceError}</div>}
              {traceResult ? <JsonBlock value={traceResult} /> : <TracePanel agentSteps={agentSteps} />}
            </section>
          )}

          {activeView === "evaluation" && (
            <section className="foundation">
              <PanelHeading kicker="QUALITY GATES" title="Latest policy evaluation" />
              <div className="service-row">
                <div className="service-symbol"><BarChart3 size={18} /></div>
                <div className="service-copy">
                  <strong>Deterministic smoke report</strong>
                  <span>Read from data/evaluation/generated/policy_latest.json</span>
                </div>
                <button className="simulate-button" type="button" onClick={loadEvaluation} disabled={!health}>
                  Load report
                </button>
              </div>
              {evaluationError && <div className="lookup-error" role="alert">{evaluationError}</div>}
              {evaluationReport?.metrics && (
                <div className="metric-grid">
                  {Object.entries(evaluationReport.metrics).map(([key, value]) => (
                    <div key={key}>
                      <span>{key.replaceAll("_", " ").toUpperCase()}</span>
                      <strong>{Number.isInteger(value) ? value : value.toFixed(3)}</strong>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          <div className="service-row service-strip global-status">
            <div className="service-symbol"><Activity size={18} /></div>
            <div className="service-copy">
              <strong>API and database</strong>
              <span>{health?.service ?? "Waiting for local API"}</span>
            </div>
            <span className={`service-state${health ? " is-ready" : ""}`}>
              {health ? "READY" : apiUnavailable ? "OFFLINE" : "PENDING"}
            </span>
          </div>

          <footer className="workspace-footer">
            <span>OPEN-SOURCE COMPONENTS</span>
            <span>LOCAL-FIRST / SYNTHETIC DATA</span>
            <span>NO MODEL REQUIRED FOR FOUNDATION</span>
          </footer>
        </div>
      </section>
    </main>
  );
}

function PanelHeading({ kicker, title }: { kicker: string; title: string }) {
  return (
    <div className="section-heading">
      <div>
        <p className="section-kicker">{kicker}</p>
        <h2>{title}</h2>
      </div>
      <span className="phase-badge">LOCAL DATA</span>
    </div>
  );
}

function TracePanel({ agentSteps }: { agentSteps: AgentChatResponse["trajectory"] }) {
  if (!agentSteps.length) {
    return <p className="empty-state inside">No agent trajectory loaded yet.</p>;
  }
  return (
    <div className="agent-trajectory" aria-label="Agent workflow trajectory">
      <span className="context-label">WORKFLOW TRACE</span>
      <ol>
        {agentSteps.map((step, index) => (
          <li key={`${step.node}-${index}`} title={step.detail}>
            {step.node.replaceAll("_", " ")}
          </li>
        ))}
      </ol>
    </div>
  );
}

function SourcePanel({ supportingDocuments }: { supportingDocuments: SearchHit[] }) {
  if (!supportingDocuments.length) {
    return <p className="empty-state inside">No supporting sources loaded yet.</p>;
  }
  return (
    <div className="supporting-sources">
      <span className="context-label">SUPPORTING SOURCES</span>
      <ul>
        {supportingDocuments.map((document) => (
          <li key={document.document_id}>
            <div>
              <strong>{document.title}</strong>
              <span>{document.source_system} / {document.source_record_id}</span>
            </div>
            <small>
              BM25 {document.bm25_rank ?? "-"} / VECTOR {document.vector_rank ?? "-"} / RRF {document.rrf_rank}
            </small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ResultList<T>({
  items,
  emptyText,
  renderItem,
}: {
  items: T[];
  emptyText: string;
  renderItem: (item: T) => React.ReactNode;
}) {
  if (!items.length) return <p className="empty-state inside">{emptyText}</p>;
  return <div className="result-list">{items.map(renderItem)}</div>;
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}
