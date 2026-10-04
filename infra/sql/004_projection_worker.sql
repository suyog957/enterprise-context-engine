-- Outbox processing: retry scheduling, stale-claim recovery and a dead-letter state.
ALTER TABLE projection_outbox
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE projection_outbox ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
ALTER TABLE projection_outbox DROP CONSTRAINT IF EXISTS projection_outbox_status_check;
ALTER TABLE projection_outbox ADD CONSTRAINT projection_outbox_status_check
    CHECK (status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER'));

DROP INDEX IF EXISTS idx_projection_outbox_pending;
CREATE INDEX IF NOT EXISTS idx_projection_outbox_due
    ON projection_outbox(next_attempt_at)
    WHERE status IN ('PENDING', 'FAILED', 'PROCESSING');

-- Ledger of events applied to each projection target (e.g. one graph version), so
-- projector retries and rebuild replays are idempotent and auditable.
CREATE TABLE IF NOT EXISTS projection_applied_event (
    projection TEXT NOT NULL,
    target_uri TEXT NOT NULL,
    event_id UUID NOT NULL REFERENCES projection_outbox(event_id),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (projection, target_uri, event_id)
);
