BEGIN;

ALTER TABLE job_postings
  ADD COLUMN IF NOT EXISTS domain_classification_method text;

CREATE INDEX IF NOT EXISTS idx_job_postings_unresolved_domain
  ON job_postings (ingestion_source, role_id)
  WHERE data_tier = 1 AND status = 'raw' AND domain IS NULL;

CREATE OR REPLACE VIEW domain_classification_funnel AS
SELECT
  ingestion_source,
  COALESCE(domain_classification_method, 'unclassified') AS classification_method,
  COALESCE(domain, 'unresolved') AS domain,
  COUNT(*)::bigint AS jobs
FROM job_postings
WHERE data_tier = 1 AND status = 'raw'
GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW unresolved_domain_titles AS
SELECT
  LOWER(BTRIM(r.role_name)) AS normalized_title,
  jp.ingestion_source,
  COUNT(*)::bigint AS active_jobs,
  MAX(jp.last_seen_at) AS last_seen_at
FROM job_postings jp
JOIN roles r ON r.role_id = jp.role_id
WHERE jp.data_tier = 1 AND jp.status = 'raw' AND jp.domain IS NULL
GROUP BY 1, 2
ORDER BY active_jobs DESC;

COMMIT;
