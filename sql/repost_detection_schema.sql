-- repost_detection_schema.sql
-- Run once on the droplet to add expired_reason column and job_posting_events table.
-- After running: expire_jobs.py and ingest_jobs.py will start populating events.
-- Backfill of historical 'disappeared' events is at the bottom.

-- ── 1. expired_reason column ──────────────────────────────────────────────────
-- Distinguishes natural cron expiry from mass-cleanup/filter runs.
-- expire_jobs.py sets 'natural_cron'; manual cleanup scripts set 'mass_cleanup'.
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS expired_reason TEXT;

COMMENT ON COLUMN job_postings.expired_reason IS
  'natural_cron = expired by normal daily cron; mass_cleanup = force-expired by a filter/cleanup run';

-- ── 2. job_posting_events table ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_posting_events (
    event_id    BIGSERIAL PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES job_postings(job_id),
    event_type  TEXT NOT NULL CHECK (event_type IN ('appeared', 'disappeared', 'reappeared')),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    gap_days    INT,         -- days since last_seen_at (for reappeared events)
    source      TEXT,        -- ingestion_source at time of event
    posted_date DATE         -- posted_date of the job at time of event
);

CREATE INDEX IF NOT EXISTS idx_jpe_job_id       ON job_posting_events (job_id);
CREATE INDEX IF NOT EXISTS idx_jpe_event_type   ON job_posting_events (event_type);
CREATE INDEX IF NOT EXISTS idx_jpe_observed_at  ON job_posting_events (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_jpe_job_event    ON job_posting_events (job_id, event_type, observed_at DESC);

-- Make event writes idempotent.  This also makes the historical backfill below
-- safe to run more than once.
CREATE UNIQUE INDEX IF NOT EXISTS uq_jpe_job_event_observed
    ON job_posting_events (job_id, event_type, observed_at);

COMMENT ON TABLE job_posting_events IS
  'Forward-looking repost detection. appeared=new job, disappeared=natural expiry, reappeared=reopened after expiry.';

-- ── 3. Backfill historical disappeared events ─────────────────────────────────
-- One 'disappeared' event per naturally expired job (last_seen_at as observed_at).
-- Excludes the 2026-05-09 mass-cleanup spike (89K jobs force-expired in one day).
-- Excludes jobs without last_seen_at (can't determine when they disappeared).
INSERT INTO job_posting_events (job_id, event_type, observed_at, source, posted_date)
SELECT
    jp.job_id,
    'disappeared',
    jp.last_seen_at,
    jp.ingestion_source,
    jp.posted_date
FROM job_postings jp
WHERE jp.status = 'expired'
  AND jp.last_seen_at IS NOT NULL
  AND DATE(jp.last_seen_at) != '2026-05-09'   -- skip mass-cleanup spike
ON CONFLICT DO NOTHING;

-- Show what we inserted
SELECT
    event_type,
    COUNT(*)          AS events_inserted,
    MIN(observed_at)  AS earliest,
    MAX(observed_at)  AS latest
FROM job_posting_events
GROUP BY event_type;
