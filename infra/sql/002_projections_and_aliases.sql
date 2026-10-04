-- Projection bookkeeping: which versioned named graph / search index is current.
CREATE TABLE IF NOT EXISTS projection_state (
    projection TEXT PRIMARY KEY,
    version BIGINT NOT NULL CHECK (version >= 1),
    target_uri TEXT NOT NULL,
    content_hash TEXT,
    item_count BIGINT,
    source_watermark TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('PUBLISHED', 'FAILED')),
    published_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS projection_history (
    projection TEXT NOT NULL,
    version BIGINT NOT NULL,
    target_uri TEXT NOT NULL,
    content_hash TEXT,
    item_count BIGINT,
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at TIMESTAMPTZ,
    PRIMARY KEY (projection, version)
);

-- Query-time entity resolution: every observed supplier name, normalized with the
-- same rules as the batch resolver, indexed for trigram candidate retrieval.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS supplier_alias (
    source_system TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    canonical_entity_id TEXT REFERENCES canonical_supplier(canonical_entity_id),
    candidate_entity_id TEXT REFERENCES canonical_supplier(canonical_entity_id),
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    resolution_method TEXT NOT NULL,
    confidence_score NUMERIC(5, 4) NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    review_required BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (source_system, source_record_id),
    FOREIGN KEY (source_system, source_record_id)
        REFERENCES source_record(source_system, source_record_id)
);

CREATE INDEX IF NOT EXISTS idx_supplier_alias_trgm
    ON supplier_alias USING gin (normalized_alias gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_supplier_alias_entity
    ON supplier_alias(canonical_entity_id);
