// Mirrors the FastAPI/Pydantic response contracts.

export type ActionStatus = "AVAILABLE" | "APPROVAL_REQUIRED" | "BLOCKED";
export type AgentStatus = "ANSWERED" | "PARTIAL" | "NEEDS_CLARIFICATION" | "UNAVAILABLE";
export type Confidence = "HIGH" | "REDUCED" | "LOW";

export interface Principal {
  principal_id: string;
  display_name: string;
  buyer_id: string | null;
  roles: string[];
  business_unit_ids: string[];
  approval_limit_minor: number;
}

export interface PolicyRuleRef {
  rule_id: string;
  document_id: string;
  reason_code: string;
}

export interface PolicyDecision {
  allowed: boolean;
  approval_required: boolean;
  reason_codes: string[];
  explanations: string[];
  policy_version: string;
  policy_rules: PolicyRuleRef[];
}

export interface ActionAvailability {
  action: string;
  status: ActionStatus;
  reason_codes: string[];
  explanations: string[];
  label: string | null;
  executable: boolean;
  execution_endpoint: string | null;
  preconditions: string[];
  effects: string[];
  required_permissions: string[];
  policy_rules: PolicyRuleRef[];
}

export interface AllowedActionsResponse {
  requisition_id: string;
  available_actions: string[];
  approval_required_actions: string[];
  actions: ActionAvailability[];
  policy_version: string;
}

export interface ResolvedEntity {
  entity_type: string;
  entity_id: string;
  label: string;
  mention: string;
  confidence: number;
  match_band: string | null;
  method: string | null;
  review_required: boolean;
}

export interface Fact {
  subject: string;
  predicate: string;
  value: string;
  source: string;
  source_system: string | null;
  source_record_id: string | null;
}

export interface DocumentRef {
  document_id: string;
  title: string;
  snippet: string;
  document_type: string;
  source_record_id: string;
  rrf_rank: number;
  bm25_rank: number | null;
  vector_rank: number | null;
  untrusted: boolean;
  flagged_instructions: boolean;
}

export interface PolicyResult {
  action: string;
  status: string;
  reason_codes: string[];
  explanations: string[];
  rules: PolicyRuleRef[];
  policy_version: string;
}

export interface ToolInvocation {
  tool: string;
  arguments: Record<string, unknown>;
  status: string;
  duration_ms: number;
  attempts: number;
  error: string | null;
  output?: unknown;
}

export interface ContextEnvelope {
  question: string;
  intent: string;
  routes: string[];
  entities: ResolvedEntity[];
  relationships: Array<{ subject: string; predicate: string; object: string; source: string }>;
  facts: Fact[];
  documents: DocumentRef[];
  policies: PolicyResult[];
  allowed_actions: ActionAvailability[];
  provenance: Array<{ entity_id: string; source_system: string; source_record_id: string; detail: string }>;
  records: Array<Record<string, unknown>>;
  freshness: { retrieved_at: string; graph_projection: string | null; policy_version: string | null };
  confidence: Confidence;
  warnings: string[];
  clarification: string | null;
  tool_calls: ToolInvocation[];
}

export interface TraceStep {
  node: string;
  status: string;
  detail: string;
  duration_ms: number;
}

export interface PurchaseOrderProposal {
  requisition_id: string;
  status: "CONFIRMATION_REQUIRED" | "APPROVAL_REQUIRED" | "NOT_PERMITTED";
  reason_codes: string[];
  explanations: string[];
  execution_endpoint: string;
  approval_endpoint: string | null;
  requires_idempotency_key: boolean;
  dry_run: boolean;
  note: string;
}

export interface AgentResponse {
  request_id: string;
  trace_id: string | null;
  status: AgentStatus;
  intent: string;
  answer: string;
  confidence: Confidence;
  citations: Array<{ kind: string; ref: string; label: string }>;
  requisition_id: string | null;
  available_actions: string[];
  proposed_action: PurchaseOrderProposal | null;
  plan: {
    goal: string;
    steps: Array<{ tool: string; arguments: Record<string, unknown>; purpose: string }>;
    requested_action: string | null;
    rejected_steps: string[];
  } | null;
  context: ContextEnvelope;
  trajectory: TraceStep[];
  tool_calls: ToolInvocation[];
  warnings: string[];
  errors: string[];
}

export interface RequisitionContext {
  requisition_id: string;
  supplier_source_id: string;
  canonical_supplier_id: string | null;
  buyer_id: string;
  buyer_name: string;
  business_unit_id: string;
  state: string;
  amount: string;
  currency: string;
  product_ids: string[];
  categories: string[];
  supplier: {
    canonical_entity_id: string | null;
    preferred_name: string | null;
    status: string;
    risk_rating: string;
    approved_categories: string[];
  };
  active_contract_ids: string[];
  row_version: number;
}

export interface ActionSimulation {
  requisition_id: string;
  decision: PolicyDecision;
  available: boolean;
  approval_required: boolean;
  dry_run: boolean;
}

export interface ApprovalRequestResult {
  approval_id: string;
  requisition_id: string;
  status: string;
  expires_at: string;
  policy_version: string;
  reason_codes: string[];
}

export interface PurchaseOrderExecution {
  requisition_id: string;
  purchase_order_id: string | null;
  status: "SIMULATED" | "DENIED" | "APPROVAL_REQUIRED" | "CREATED";
  dry_run: boolean;
  decision: PolicyDecision;
  idempotent_replay: boolean;
}

export interface EntityCandidate {
  canonical_entity_id: string;
  entity_type: string;
  preferred_name: string;
  status: string;
  risk_rating: string;
  matched_alias: string | null;
  source_system: string | null;
  confidence_score: number | null;
  match_band: string | null;
  match_method: string | null;
  review_required: boolean;
}

export interface EntityContextResponse {
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
}

export interface GraphTemplateResult {
  query_type: string;
  rows: Array<Record<string, string>>;
  boolean: boolean | null;
  graph_uri: string | null;
  template: string;
}

export interface TraceResponse {
  trace_id: string;
  agent_run: {
    request_id: string;
    question: string;
    intent: string;
    status: string;
    answer: string;
    trajectory: TraceStep[];
    tool_calls: ToolInvocation[];
    created_at: string;
  } | null;
  events: Array<Record<string, unknown>>;
  spans: Array<{
    span_id: string;
    parent_span_id: string | null;
    operation: string;
    service: string;
    start_offset_ms: number;
    duration_ms: number;
    tags: Record<string, unknown>;
  }>;
  span_source: string;
  jaeger_url: string | null;
}

export interface Gate {
  gate: string;
  metric: string;
  comparison: string;
  threshold: number;
  actual: number;
  passed: boolean;
  critical: boolean;
}

export interface EvaluationReport {
  metadata?: Record<string, string | number>;
  metrics?: Record<string, unknown>;
  gates?: Gate[];
  critical_failures?: string[];
}

export interface DataQualityReport {
  issues: Array<{ issue: string; records: number }>;
  scope: string;
  graph_validation: Record<string, unknown>;
}
