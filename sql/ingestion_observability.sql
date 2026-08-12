BEGIN;

CREATE TABLE IF NOT EXISTS ingestion_crawl_runs (
    run_id text PRIMARY KEY,
    source text NOT NULL,
    orchestration_run_id text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN
        ('running', 'complete_nonzero', 'complete_zero', 'partial_failure', 'source_failure')),
    jobs_fetched integer NOT NULL DEFAULT 0,
    jobs_written integer NOT NULL DEFAULT 0,
    errors integer NOT NULL DEFAULT 0,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_ingestion_crawl_runs_source_finished
    ON ingestion_crawl_runs (source, finished_at DESC);

CREATE TABLE IF NOT EXISTS ingestion_tenant_runs (
    run_id text NOT NULL REFERENCES ingestion_crawl_runs(run_id) ON DELETE CASCADE,
    source text NOT NULL,
    crawl_tenant text NOT NULL,
    status text NOT NULL CHECK (status IN ('complete_nonzero', 'complete_zero', 'partial_failure', 'failed')),
    jobs_fetched integer NOT NULL DEFAULT 0,
    pages_fetched integer,
    pages_expected integer,
    errors integer NOT NULL DEFAULT 0,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, source, crawl_tenant)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_tenant_runs_complete
    ON ingestion_tenant_runs (source, crawl_tenant, run_id);

ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS canonical_opportunity_id text;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS source_checked_at timestamptz;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS source_http_status integer;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS source_validation_note text;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS crawl_tenant text;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS enrichment_input_hash text;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS expired_reason text;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS scope_status text;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS scope_rule_id text;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS scope_confidence double precision;

CREATE TABLE IF NOT EXISTS role_scope_decisions (
    source text NOT NULL,
    source_id text NOT NULL,
    title text NOT NULL,
    company text,
    crawl_tenant text,
    status text NOT NULL CHECK (status IN
        ('accepted_core', 'accepted_evidence', 'quarantine', 'rejected')),
    rule_id text NOT NULL,
    domain text,
    role_category text,
    confidence double precision NOT NULL,
    positive_signals jsonb NOT NULL DEFAULT '[]'::jsonb,
    negative_signals jsonb NOT NULL DEFAULT '[]'::jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    seen_count integer NOT NULL DEFAULT 1,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_role_scope_decisions_review
    ON role_scope_decisions (status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_role_scope_decisions_rule
    ON role_scope_decisions (rule_id, status);

CREATE TABLE IF NOT EXISTS job_posting_events (
    event_id bigserial PRIMARY KEY,
    job_id text NOT NULL REFERENCES job_postings(job_id),
    event_type text NOT NULL CHECK (event_type IN ('appeared','disappeared','reappeared')),
    observed_at timestamptz NOT NULL DEFAULT now(),
    gap_days integer,
    source text,
    posted_date date
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_jpe_job_event_observed
    ON job_posting_events (job_id, event_type, observed_at);
CREATE INDEX IF NOT EXISTS idx_jpe_job_event
    ON job_posting_events (job_id, event_type, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_postings_canonical_opportunity
    ON job_postings (canonical_opportunity_id)
    WHERE canonical_opportunity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_job_postings_crawl_tenant
    ON job_postings (ingestion_source, crawl_tenant, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_postings_source_validation
    ON job_postings (source_checked_at NULLS FIRST, last_seen_at DESC)
    WHERE data_tier = 1 AND status = 'raw' AND job_url IS NOT NULL;

COMMIT;
