-- Partial indexes aligned with the public feed's common filter/order paths.
-- CONCURRENTLY keeps deployment and nightly publication non-blocking.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jp_public_domain_posted
  ON job_postings (domain, posted_date DESC, job_id)
  WHERE is_public=true AND data_tier=1;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jp_public_role_posted
  ON job_postings (role_category, posted_date DESC, job_id)
  WHERE is_public=true AND data_tier=1;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jp_public_workplace_posted
  ON job_postings (workplace_type, posted_date DESC, job_id)
  WHERE is_public=true AND data_tier=1;
