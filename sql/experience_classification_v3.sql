BEGIN;

ALTER TABLE job_postings
  ADD COLUMN IF NOT EXISTS experience_level_v3 text,
  ADD COLUMN IF NOT EXISTS experience_level_confidence double precision,
  ADD COLUMN IF NOT EXISTS experience_level_evidence jsonb,
  ADD COLUMN IF NOT EXISTS experience_classifier_version text,
  ADD COLUMN IF NOT EXISTS experience_classified_at timestamptz,
  ADD COLUMN IF NOT EXISTS management_level text;

CREATE INDEX IF NOT EXISTS idx_job_postings_experience_v3
  ON job_postings (experience_level_v3)
  WHERE status = 'raw' AND data_tier = 1;

COMMIT;
