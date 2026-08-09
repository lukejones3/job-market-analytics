BEGIN;

-- Keep the routed-ATS handoff self-contained for new environments. Existing
-- deployments already own this table through migrate_ats_candidates.py.
CREATE TABLE IF NOT EXISTS ats_tenants_candidates (
    ats text NOT NULL,
    tenant text NOT NULL,
    server text,
    source text NOT NULL,
    company_name text,
    us_jobs_count integer DEFAULT 0,
    data_ml_jobs_count integer DEFAULT 0,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    last_validated_at timestamptz,
    status text NOT NULL DEFAULT 'pending',
    PRIMARY KEY (ats, tenant)
);

CREATE INDEX IF NOT EXISTS idx_atc_status ON ats_tenants_candidates (status);
CREATE INDEX IF NOT EXISTS idx_atc_ats_status ON ats_tenants_candidates (ats, status);

CREATE TABLE IF NOT EXISTS career_host_candidates (
    candidate_id bigserial PRIMARY KEY,
    company_id text,
    company_name text NOT NULL,
    company_key text NOT NULL,
    official_domain text,
    careers_url text,
    discovered_url text,
    discovery_source text NOT NULL,
    lead_job_count integer NOT NULL DEFAULT 0,
    resolver_confidence double precision,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','resolved','needs_review','routed','rejected')),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    last_attempted_at timestamptz,
    resolved_at timestamptz,
    UNIQUE (company_key)
);

CREATE INDEX IF NOT EXISTS idx_career_host_candidates_queue
    ON career_host_candidates (status, lead_job_count DESC, discovered_at);

CREATE TABLE IF NOT EXISTS career_hosts (
    host_id text PRIMARY KEY,
    company_id text,
    company_name text NOT NULL,
    company_key text NOT NULL,
    official_domain text,
    careers_url text NOT NULL,
    jobs_host text NOT NULL,
    platform text NOT NULL DEFAULT 'custom',
    tenant_token text,
    extraction_strategy text NOT NULL DEFAULT 'sitemap_jsonld'
        CHECK (extraction_strategy IN ('ats_router','sitemap_jsonld','oracle_cloud','api_recipe','manual')),
    api_recipe jsonb NOT NULL DEFAULT '{}'::jsonb,
    discovery_source text NOT NULL,
    resolver_confidence double precision NOT NULL DEFAULT 0,
    identity_status text NOT NULL DEFAULT 'pending'
        CHECK (identity_status IN ('pending','verified','needs_review','rejected')),
    status text NOT NULL DEFAULT 'shadow'
        CHECK (status IN ('shadow','active','quarantined','disabled','routed')),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_clean_crawl_at timestamptz,
    activated_at timestamptz,
    last_crawled_at timestamptz,
    last_success_at timestamptz,
    last_nonempty_at timestamptz,
    last_job_count integer NOT NULL DEFAULT 0,
    failure_streak integer NOT NULL DEFAULT 0,
    next_crawl_at timestamptz,
    etag text,
    last_modified text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_key, careers_url)
);

CREATE INDEX IF NOT EXISTS idx_career_hosts_crawl_queue
    ON career_hosts (status, next_crawl_at, last_crawled_at)
    WHERE status IN ('shadow','active');
CREATE INDEX IF NOT EXISTS idx_career_hosts_platform
    ON career_hosts (platform, status);

CREATE TABLE IF NOT EXISTS career_host_runs (
    run_id text PRIMARY KEY,
    host_id text NOT NULL REFERENCES career_hosts(host_id) ON DELETE CASCADE,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','complete_nonzero','complete_zero','partial_failure','failed','quality_rejected')),
    pages_discovered integer NOT NULL DEFAULT 0,
    pages_fetched integer NOT NULL DEFAULT 0,
    postings_parsed integer NOT NULL DEFAULT 0,
    target_jobs integer NOT NULL DEFAULT 0,
    explicit_us_jobs integer NOT NULL DEFAULT 0,
    accepted_jobs integer NOT NULL DEFAULT 0,
    written_jobs integer NOT NULL DEFAULT 0,
    duplicate_jobs integer NOT NULL DEFAULT 0,
    identity_mismatches integer NOT NULL DEFAULT 0,
    foreign_rejections integer NOT NULL DEFAULT 0,
    quality_rejections integer NOT NULL DEFAULT 0,
    errors integer NOT NULL DEFAULT 0,
    rejection_reasons jsonb NOT NULL DEFAULT '{}'::jsonb,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_career_host_runs_host_started
    ON career_host_runs (host_id, started_at DESC);

CREATE TABLE IF NOT EXISTS career_discovery_queries (
    query_id bigserial PRIMARY KEY,
    query_text text NOT NULL,
    query_kind text NOT NULL,
    company_key text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    result_count integer NOT NULL DEFAULT 0,
    candidate_url text,
    candidate_score double precision,
    resolved_host_id text REFERENCES career_hosts(host_id),
    response_metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_career_discovery_queries_company
    ON career_discovery_queries (company_key, requested_at DESC);

COMMIT;
