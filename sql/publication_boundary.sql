-- Atomic public-feed boundary. Ingestion and enrichment may freely mutate `raw`
-- rows; consumers see only the last snapshot that passed publication checks.
ALTER TABLE job_postings
  ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_job_postings_public_feed
  ON job_postings (posted_date DESC, job_id)
  WHERE is_public = true;

CREATE TABLE IF NOT EXISTS publication_runs (
  publication_id BIGSERIAL PRIMARY KEY,
  published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  prior_count INTEGER NOT NULL,
  candidate_count INTEGER NOT NULL,
  activated_count INTEGER NOT NULL,
  deactivated_count INTEGER NOT NULL
);

-- Safe first-deploy seed. Later changes happen only in publish_snapshot.py.
UPDATE job_postings jp
SET is_public = true, published_at = COALESCE(published_at, now())
WHERE NOT EXISTS (SELECT 1 FROM job_postings WHERE is_public = true)
  AND jp.status = 'raw'
  AND jp.data_tier = 1
  AND COALESCE(jp.source, '') <> 'adzuna'
  AND jp.company_id IS NOT NULL
  AND jp.role_id IS NOT NULL
  AND jp.domain IS NOT NULL
  AND jp.role_category IS NOT NULL
  AND jp.experience_level IS NOT NULL
  AND jp.embedding IS NOT NULL
  AND length(COALESCE(jp.description_text, '')) >= 100
  AND COALESCE(jp.loc_country, 'us') <> 'foreign';
