/* ============================================================
   MULTI-VERTICAL FOUNDATION
   Adds vertical awareness to existing skills infrastructure
   and domain classification columns to job_postings.

   Safe to re-run — all operations use IF NOT EXISTS / IF EXISTS.
   Does NOT modify existing data, only adds columns/indexes.
   ============================================================ */


/* ── 1. SKILLS: vertical + also_in ─────────────────────────── */

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS vertical      TEXT,
    ADD COLUMN IF NOT EXISTS also_in       TEXT[];

COMMENT ON COLUMN skills.vertical IS
    'Primary vertical: data_ml | engineering | finance | marketing | product | sales | design | ops';
COMMENT ON COLUMN skills.also_in IS
    'Secondary verticals where this skill is relevant';

CREATE INDEX IF NOT EXISTS idx_skills_vertical
    ON skills(vertical);


/* ── 2. JOB_POSTINGS: domain classification ─────────────────── */

ALTER TABLE job_postings
    ADD COLUMN IF NOT EXISTS domain               TEXT,
    ADD COLUMN IF NOT EXISTS domain_secondary     TEXT[],
    ADD COLUMN IF NOT EXISTS domain_classified_at TIMESTAMPTZ;

COMMENT ON COLUMN job_postings.domain IS
    'Primary vertical for this job: data_ml | engineering | finance | marketing | product | sales | design | ops';
COMMENT ON COLUMN job_postings.domain_secondary IS
    'Additional verticals this job overlaps with (e.g. ML Engineer -> [engineering])';
COMMENT ON COLUMN job_postings.domain_classified_at IS
    'Timestamp of last domain classification run';

CREATE INDEX IF NOT EXISTS idx_job_postings_domain
    ON job_postings(domain);

CREATE INDEX IF NOT EXISTS idx_job_postings_domain_status
    ON job_postings(domain, status);


/* ── 3. API_KEYS: per-user vertical preference ──────────────── */

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS vertical_preference TEXT;

COMMENT ON COLUMN api_keys.vertical_preference IS
    'User-selected vertical filter. NULL = all. Valid: data_ml | engineering | finance | marketing | product | sales | design | ops | all';
