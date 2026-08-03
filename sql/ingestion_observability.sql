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

ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS canonical_opportunity_id text;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS crawl_tenant text;
CREATE INDEX IF NOT EXISTS idx_job_postings_canonical_opportunity
    ON job_postings (canonical_opportunity_id)
    WHERE canonical_opportunity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_job_postings_crawl_tenant
    ON job_postings (ingestion_source, crawl_tenant, last_seen_at);

COMMIT;
