BEGIN;

-- Employer-feed registry. A partner is one authoritative employer feed, not
-- an aggregator. partner_key is durable: it becomes crawl_tenant on every job
-- written from the feed, so renaming it would orphan lifecycle ownership.
CREATE TABLE IF NOT EXISTS employer_feed_partners (
    partner_id bigserial PRIMARY KEY,
    partner_key text NOT NULL UNIQUE
        CHECK (partner_key ~ '^[a-z0-9][a-z0-9_-]{0,99}$'),
    name text NOT NULL,
    feed_url text NOT NULL UNIQUE,
    feed_format text NOT NULL DEFAULT 'auto'
        CHECK (feed_format IN ('auto', 'json', 'csv')),
    employer_name text NOT NULL,
    employer_domain text
        CHECK (employer_domain IS NULL OR employer_domain !~ '^[a-z]+://'),
    employer_company_id text,
    -- Name of an environment variable containing a bearer token. Never store
    -- the credential itself in this registry.
    auth_env_var text
        CHECK (auth_env_var IS NULL OR auth_env_var ~ '^[A-Z][A-Z0-9_]*$'),
    enabled boolean NOT NULL DEFAULT true,
    notes text,
    last_run_at timestamptz,
    last_status text
        CHECK (last_status IS NULL OR last_status IN
            ('complete_nonzero', 'complete_zero', 'partial_failure', 'failed')),
    last_rows_seen integer NOT NULL DEFAULT 0,
    last_rows_ok integer NOT NULL DEFAULT 0,
    last_rows_bad integer NOT NULL DEFAULT 0,
    last_jobs_written integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_employer_feed_partners_enabled
    ON employer_feed_partners (enabled, partner_key)
    WHERE enabled;

CREATE INDEX IF NOT EXISTS idx_employer_feed_partners_employer
    ON employer_feed_partners (lower(employer_name), partner_key);

-- One row per partner per registry run. The source-level and tenant-level
-- accounting remains in ingestion_crawl_runs / ingestion_tenant_runs; this
-- table preserves the exact feed row math (including malformed rows).
CREATE TABLE IF NOT EXISTS employer_feed_runs (
    run_id text NOT NULL,
    partner_key text NOT NULL REFERENCES employer_feed_partners(partner_key) ON DELETE CASCADE,
    orchestration_run_id text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL
        CHECK (status IN ('complete_nonzero', 'complete_zero', 'partial_failure', 'failed')),
    rows_seen integer NOT NULL DEFAULT 0,
    rows_ok integer NOT NULL DEFAULT 0,
    rows_bad integer NOT NULL DEFAULT 0,
    rows_non_target integer NOT NULL DEFAULT 0,
    duplicate_rows integer NOT NULL DEFAULT 0,
    jobs_considered integer NOT NULL DEFAULT 0,
    jobs_written integer NOT NULL DEFAULT 0,
    jobs_skipped integer NOT NULL DEFAULT 0,
    jobs_write_errors integer NOT NULL DEFAULT 0,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, partner_key)
);

CREATE INDEX IF NOT EXISTS idx_employer_feed_runs_partner_finished
    ON employer_feed_runs (partner_key, finished_at DESC);

COMMIT;
