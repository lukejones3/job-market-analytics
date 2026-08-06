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

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'job_postings_experience_v3_level_check') THEN
    ALTER TABLE job_postings ADD CONSTRAINT job_postings_experience_v3_level_check
      CHECK (experience_level_v3 IS NULL OR experience_level_v3 IN
        ('entry', 'associate', 'mid', 'senior', 'unknown')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'job_postings_experience_v3_confidence_check') THEN
    ALTER TABLE job_postings ADD CONSTRAINT job_postings_experience_v3_confidence_check
      CHECK (experience_level_confidence IS NULL OR
        experience_level_confidence BETWEEN 0 AND 1) NOT VALID;
  END IF;
END $$;

COMMIT;
