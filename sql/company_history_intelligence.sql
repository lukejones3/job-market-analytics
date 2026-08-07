-- Company history that distinguishes crawler churn and ATS-wide date refreshes
-- from role-level repost signals. All source postings and events are retained.

CREATE OR REPLACE VIEW vw_repost_events_classified AS
WITH reappearances AS (
    SELECT e.event_id, e.job_id, e.observed_at, e.posted_date,
           jp.ingestion_source, jp.crawl_tenant, jp.company_id,
           c.company_name, r.role_name,
           prior.observed_at AS disappeared_at,
           prior.posted_date AS prior_posted_date,
           EXTRACT(EPOCH FROM (e.observed_at-prior.observed_at))/86400.0 AS observed_gap_days
    FROM job_posting_events e
    JOIN job_postings jp USING(job_id)
    LEFT JOIN companies c USING(company_id)
    LEFT JOIN roles r USING(role_id)
    JOIN LATERAL (
        SELECT d.observed_at, d.posted_date
        FROM job_posting_events d
        WHERE d.job_id=e.job_id AND d.event_type='disappeared'
          AND d.observed_at < e.observed_at
        ORDER BY d.observed_at DESC LIMIT 1
    ) prior ON true
    WHERE e.event_type='reappeared' AND e.posted_date > prior.posted_date
), cohorts AS (
    SELECT *, count(*) OVER (
        PARTITION BY company_id, ingestion_source, observed_at::date, posted_date
    ) AS same_refresh_cohort
    FROM reappearances
)
SELECT *, CASE
    WHEN same_refresh_cohort >= 5 THEN 'bulk_ats_date_refresh'
    WHEN observed_gap_days < 1 THEN 'crawler_churn'
    ELSE 'individual_repost_signal'
END AS signal_class
FROM cohorts;

CREATE OR REPLACE VIEW vw_company_history_clean AS
WITH named AS (
    SELECT jp.*, c.company_name,
      regexp_replace(lower(c.company_name),'[^a-z0-9]+','','g') AS company_key
    FROM job_postings jp JOIN companies c USING(company_id)
    WHERE jp.data_tier=1
      AND c.company_name !~* '^(senior|sr\\.?|lead|principal|staff|junior|jr\\.?|associate)?[[:space:]]*(data|analytics|business intelligence|bi|machine learning|ml|ai)[[:space:]]+(engineer|analyst|scientist|architect|developer|manager|consultant)s?$'
), preferred_name AS (
    SELECT DISTINCT ON (company_key) company_key, company_name
    FROM (SELECT company_key, company_name, count(*) n FROM named GROUP BY 1,2) counts
    ORDER BY company_key, n DESC, length(company_name) DESC
), company_base AS (
    SELECT n.company_key AS company_id, p.company_name,
           min(jp.date_found) AS coverage_started_at,
           max(jp.last_seen_at) AS last_observed_at,
           count(*) AS source_postings,
           count(DISTINCT COALESCE(jp.canonical_opportunity_id,jp.job_id)) AS observed_opportunities,
           count(*) FILTER (WHERE jp.status='raw' AND jp.is_public) AS active_source_postings,
           count(DISTINCT COALESCE(jp.canonical_opportunity_id,jp.job_id))
             FILTER (WHERE jp.status='raw' AND jp.is_public) AS active_opportunities,
           count(*) FILTER (WHERE jp.salary_min IS NOT NULL OR jp.salary_max IS NOT NULL) AS salary_disclosed,
           percentile_cont(.5) WITHIN GROUP (ORDER BY
             EXTRACT(EPOCH FROM (jp.last_seen_at-jp.date_found))/86400.0)
             FILTER (WHERE jp.status='expired'
               AND jp.last_seen_at-jp.date_found >= interval '2 days') AS median_observed_lifetime_days
    FROM named jp
    JOIN preferred_name p ON p.company_key=jp.company_key
    CROSS JOIN LATERAL (SELECT jp.company_key) n
    GROUP BY n.company_key,p.company_name
), signals AS (
    SELECT regexp_replace(lower(company_name),'[^a-z0-9]+','','g') AS company_key,
      count(*) FILTER (WHERE signal_class='individual_repost_signal') AS individual_repost_signals,
      count(*) FILTER (WHERE signal_class='bulk_ats_date_refresh') AS bulk_ats_refresh_events,
      count(DISTINCT observed_at::date) FILTER (WHERE signal_class='bulk_ats_date_refresh') AS bulk_refresh_days
    FROM vw_repost_events_classified GROUP BY 1
)
SELECT b.*, COALESCE(s.individual_repost_signals,0) AS individual_repost_signals,
       COALESCE(s.bulk_ats_refresh_events,0) AS bulk_ats_refresh_events,
       COALESCE(s.bulk_refresh_days,0) AS bulk_refresh_days,
       round(100.0*b.salary_disclosed/NULLIF(b.source_postings,0),1) AS salary_transparency_pct
FROM company_base b LEFT JOIN signals s ON s.company_key=b.company_id;

CREATE TABLE IF NOT EXISTS company_daily_snapshots (
    snapshot_date date NOT NULL,
    company_key text NOT NULL,
    company_name text NOT NULL,
    active_source_postings integer NOT NULL,
    active_opportunities integer NOT NULL,
    observed_opportunities integer NOT NULL,
    salary_transparency_pct numeric(5,1),
    individual_repost_signals integer NOT NULL,
    bulk_ats_refresh_events integer NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(snapshot_date,company_key)
);

CREATE OR REPLACE FUNCTION refresh_company_daily_snapshot(day date DEFAULT current_date)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE affected integer;
BEGIN
  INSERT INTO company_daily_snapshots (
    snapshot_date,company_key,company_name,active_source_postings,
    active_opportunities,observed_opportunities,salary_transparency_pct,
    individual_repost_signals,bulk_ats_refresh_events,captured_at)
  SELECT day,company_id,company_name,active_source_postings,active_opportunities,
    observed_opportunities,salary_transparency_pct,individual_repost_signals,
    bulk_ats_refresh_events,now()
  FROM vw_company_history_clean
  ON CONFLICT(snapshot_date,company_key) DO UPDATE SET
    company_name=excluded.company_name,
    active_source_postings=excluded.active_source_postings,
    active_opportunities=excluded.active_opportunities,
    observed_opportunities=excluded.observed_opportunities,
    salary_transparency_pct=excluded.salary_transparency_pct,
    individual_repost_signals=excluded.individual_repost_signals,
    bulk_ats_refresh_events=excluded.bulk_ats_refresh_events,
    captured_at=now();
  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected;
END $$;
