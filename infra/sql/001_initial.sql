CREATE TABLE IF NOT EXISTS schema_migration (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_run (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    seed INTEGER,
    record_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS source_record (
    source_system TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    observed_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_system, source_record_id)
);

CREATE TABLE IF NOT EXISTS business_unit (
    business_unit_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_system TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_supplier (
    canonical_entity_id TEXT PRIMARY KEY,
    preferred_name TEXT NOT NULL,
    country_code CHAR(2) NOT NULL,
    postal_code TEXT,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'BLOCKED', 'SUSPENDED', 'UNDER_REVIEW')),
    risk_rating TEXT NOT NULL,
    approved_categories TEXT[] NOT NULL DEFAULT '{}',
    source_system TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_supplier_identifier (
    source_system TEXT NOT NULL,
    source_supplier_id TEXT NOT NULL,
    canonical_entity_id TEXT REFERENCES canonical_supplier(canonical_entity_id),
    source_record_id TEXT NOT NULL,
    resolution_method TEXT NOT NULL,
    confidence_score NUMERIC(5, 4) NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    review_required BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (source_system, source_supplier_id),
    FOREIGN KEY (source_system, source_record_id)
        REFERENCES source_record(source_system, source_record_id)
);

CREATE TABLE IF NOT EXISTS buyer (
    buyer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    business_unit_id TEXT NOT NULL REFERENCES business_unit(business_unit_id),
    approval_limit NUMERIC(14, 2) NOT NULL CHECK (approval_limit >= 0),
    roles TEXT[] NOT NULL DEFAULT '{}',
    source_system TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS principal (
    principal_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    buyer_id TEXT REFERENCES buyer(buyer_id),
    roles TEXT[] NOT NULL DEFAULT '{}',
    business_unit_ids TEXT[] NOT NULL DEFAULT '{}',
    approval_limit_minor BIGINT NOT NULL CHECK (approval_limit_minor >= 0),
    active BOOLEAN NOT NULL DEFAULT true,
    source_system TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_of_measure TEXT NOT NULL,
    source_system TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_requisition (
    requisition_id TEXT PRIMARY KEY,
    source_supplier_id TEXT NOT NULL,
    supplier_source_system TEXT NOT NULL DEFAULT 'ERP',
    canonical_supplier_id TEXT REFERENCES canonical_supplier(canonical_entity_id),
    buyer_id TEXT NOT NULL REFERENCES buyer(buyer_id),
    business_unit_id TEXT NOT NULL REFERENCES business_unit(business_unit_id),
    state TEXT NOT NULL CHECK (state IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'CLOSED', 'CONVERTED')),
    amount NUMERIC(14, 2) NOT NULL CHECK (amount >= 0),
    currency CHAR(3) NOT NULL,
    product_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 1,
    source_system TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_requisition_supplier_state
    ON purchase_requisition(canonical_supplier_id, state);
CREATE INDEX IF NOT EXISTS idx_requisition_business_unit_created
    ON purchase_requisition(business_unit_id, created_at DESC);

CREATE TABLE IF NOT EXISTS purchase_order (
    purchase_order_id TEXT PRIMARY KEY,
    requisition_id TEXT REFERENCES purchase_requisition(requisition_id),
    source_supplier_id TEXT NOT NULL,
    supplier_source_system TEXT NOT NULL DEFAULT 'ERP',
    canonical_supplier_id TEXT REFERENCES canonical_supplier(canonical_entity_id),
    buyer_id TEXT NOT NULL REFERENCES buyer(buyer_id),
    amount NUMERIC(14, 2) CHECK (amount IS NULL OR amount >= 0),
    currency CHAR(3) NOT NULL,
    status TEXT NOT NULL,
    data_quality_issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_system TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_purchase_order_supplier_created
    ON purchase_order(canonical_supplier_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_purchase_order_requisition
    ON purchase_order(requisition_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_order_single_conversion
    ON purchase_order(requisition_id)
    WHERE requisition_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS contract (
    contract_id TEXT PRIMARY KEY,
    source_supplier_id TEXT NOT NULL,
    supplier_source_system TEXT NOT NULL DEFAULT 'ERP',
    canonical_supplier_id TEXT REFERENCES canonical_supplier(canonical_entity_id),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    permitted_categories TEXT[] NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    source_system TEXT NOT NULL,
    CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_contract_supplier_dates
    ON contract(canonical_supplier_id, start_date, end_date);

CREATE TABLE IF NOT EXISTS idempotency_record (
    principal_id TEXT NOT NULL,
    action_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('IN_PROGRESS', 'SUCCEEDED', 'FAILED')),
    response_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, action_scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS approval_request (
    approval_id UUID PRIMARY KEY,
    requester_id TEXT NOT NULL,
    approver_id TEXT,
    action_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    arguments JSONB NOT NULL,
    resource_version BIGINT NOT NULL,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'INVALIDATED')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_approval_request_status_expiry
    ON approval_request(status, expires_at);

CREATE TABLE IF NOT EXISTS audit_event (
    event_id UUID PRIMARY KEY,
    request_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_version TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_resource_time
    ON audit_event(resource_type, resource_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS projection_outbox (
    event_id UUID PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_projection_outbox_pending
    ON projection_outbox(created_at)
    WHERE status = 'PENDING';
