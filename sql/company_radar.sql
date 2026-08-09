-- Company Radar: stable company-level hiring behavior, follows, alerts, and sourced research.
--
-- The historical series is reconstructed from the retained posting archive. Exact live
-- observations take over on the day the feature is deployed. Reappearance signals use
-- mv_repost_events_classified so bulk ATS date refreshes and crawler churn never count as
-- company-level repost behavior.

CREATE TABLE IF NOT EXISTS company_radar_daily (
    snapshot_date date NOT NULL,
    company_id text NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    domain text NOT NULL,
    active_opportunities integer NOT NULL DEFAULT 0,
    active_source_postings integer NOT NULL DEFAULT 0,
    new_opportunities_7d integer NOT NULL DEFAULT 0,
    disappeared_opportunities_7d integer NOT NULL DEFAULT 0,
    salary_transparency_pct numeric(5,1),
    median_closed_lifetime_days numeric(8,1),
    individual_repost_signals_30d integer NOT NULL DEFAULT 0,
    bulk_refresh_events_30d integer NOT NULL DEFAULT 0,
    coverage_started_at date,
    role_mix jsonb NOT NULL DEFAULT '[]'::jsonb,
    snapshot_source text NOT NULL DEFAULT 'live'
      CHECK (snapshot_source IN ('live', 'archive_reconstruction')),
    captured_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_date, company_id, domain)
);

CREATE INDEX IF NOT EXISTS ix_company_radar_daily_scope_rank
    ON company_radar_daily(domain, snapshot_date DESC, active_opportunities DESC);
CREATE INDEX IF NOT EXISTS ix_company_radar_daily_company_history
    ON company_radar_daily(company_id, domain, snapshot_date DESC);

CREATE TABLE IF NOT EXISTS user_followed_companies (
    user_id text NOT NULL REFERENCES api_keys(key_id) ON DELETE CASCADE,
    company_id text NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    alert_frequency text NOT NULL DEFAULT 'weekly'
      CHECK (alert_frequency IN ('none', 'daily', 'weekly')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, company_id)
);

CREATE INDEX IF NOT EXISTS ix_user_followed_companies_company
    ON user_followed_companies(company_id, user_id);

CREATE TABLE IF NOT EXISTS company_radar_alerts (
    alert_id bigserial PRIMARY KEY,
    user_id text NOT NULL REFERENCES api_keys(key_id) ON DELETE CASCADE,
    company_id text NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    domain text NOT NULL,
    alert_type text NOT NULL
      CHECK (alert_type IN ('hiring_surge', 'fresh_roles', 'repost_watch')),
    signal_date date NOT NULL,
    title text NOT NULL,
    detail text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, company_id, domain, alert_type, signal_date)
);

CREATE INDEX IF NOT EXISTS ix_company_radar_alerts_user_unread
    ON company_radar_alerts(user_id, read_at, signal_date DESC);

CREATE TABLE IF NOT EXISTS company_radar_alert_deliveries (
    delivery_id bigserial PRIMARY KEY,
    user_id text NOT NULL REFERENCES api_keys(key_id) ON DELETE CASCADE,
    cadence text NOT NULL CHECK (cadence IN ('daily', 'weekly')),
    digest_date date NOT NULL,
    alert_ids bigint[] NOT NULL,
    provider_message_id text,
    delivered_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, cadence, digest_date)
);

CREATE INDEX IF NOT EXISTS ix_company_radar_alert_deliveries_user
    ON company_radar_alert_deliveries(user_id, delivered_at DESC);

CREATE TABLE IF NOT EXISTS company_research_events (
    event_id bigserial PRIMARY KEY,
    company_id text NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    event_type text NOT NULL
      CHECK (event_type IN ('expansion', 'funding', 'leadership', 'layoff', 'earnings', 'hiring', 'other')),
    headline text NOT NULL,
    summary text NOT NULL,
    source_url text NOT NULL,
    source_domain text,
    source_title text,
    source_published_at timestamptz,
    provider text NOT NULL DEFAULT 'serper',
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence numeric(4,3) NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
    fetched_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '45 days'),
    UNIQUE (company_id, source_url)
);

CREATE INDEX IF NOT EXISTS ix_company_research_events_company_recent
    ON company_research_events(company_id, source_published_at DESC NULLS LAST, fetched_at DESC);

CREATE TABLE IF NOT EXISTS company_radar_research_state (
    company_id text PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
    last_attempted_at timestamptz,
    last_succeeded_at timestamptz,
    last_error text,
    source_count integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_radar_usage (
    usage_id bigserial PRIMARY KEY,
    provider text NOT NULL,
    operation text NOT NULL,
    company_id text REFERENCES companies(company_id) ON DELETE SET NULL,
    request_count integer NOT NULL DEFAULT 1,
    estimated_cost_usd numeric(10,6) NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_company_radar_usage_month
    ON company_radar_usage(provider, created_at DESC);

ALTER TABLE user_notification_prefs
    ADD COLUMN IF NOT EXISTS email_radar_enabled boolean DEFAULT true,
    ADD COLUMN IF NOT EXISTS radar_alert_frequency text DEFAULT 'weekly';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'user_notification_prefs'::regclass
      AND conname = 'user_notification_prefs_radar_frequency_check'
  ) THEN
    ALTER TABLE user_notification_prefs
      ADD CONSTRAINT user_notification_prefs_radar_frequency_check
      CHECK (radar_alert_frequency IN ('daily', 'weekly'));
  END IF;
END $$;

-- Rebuild one company/day/scope observation. `day < current_date` uses the retained
-- date_found -> last_seen_at envelope because is_public is intentionally a current-state flag.
CREATE OR REPLACE FUNCTION refresh_company_radar_snapshot(day date DEFAULT current_date)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
  affected integer;
BEGIN
  DELETE FROM company_radar_daily WHERE snapshot_date = day;

  WITH eligible AS (
    SELECT
      jp.job_id,
      jp.company_id,
      jp.domain,
      NULLIF(BTRIM(jp.role_category), '') AS role_category,
      COALESCE(jp.canonical_opportunity_id, jp.job_id) AS opportunity_id,
      COALESCE(jp.date_found, jp.posted_date) AS first_observed_date,
      MIN(COALESCE(jp.date_found, jp.posted_date)) OVER (
        PARTITION BY jp.company_id, COALESCE(jp.canonical_opportunity_id, jp.job_id)
      ) AS opportunity_first_observed_date,
      jp.last_seen_at::date AS last_observed_date,
      MAX(jp.last_seen_at::date) OVER (
        PARTITION BY jp.company_id, COALESCE(jp.canonical_opportunity_id, jp.job_id)
      ) AS opportunity_last_observed_date,
      BOOL_OR(jp.is_public) OVER (
        PARTITION BY jp.company_id, COALESCE(jp.canonical_opportunity_id, jp.job_id)
      ) AS opportunity_is_currently_public,
      jp.status,
      jp.is_public,
      (jp.salary_min_annual IS NOT NULL OR jp.salary_max_annual IS NOT NULL) AS salary_disclosed,
      CASE
        WHEN jp.last_seen_at IS NOT NULL AND COALESCE(jp.posted_date, jp.date_found) IS NOT NULL
          AND jp.last_seen_at::date >= COALESCE(jp.posted_date, jp.date_found)
        THEN (jp.last_seen_at::date - COALESCE(jp.posted_date, jp.date_found))::numeric
        ELSE NULL
      END AS lifetime_days
    FROM job_postings jp
    JOIN companies c ON c.company_id = jp.company_id
    WHERE jp.data_tier = 1
      AND jp.company_id IS NOT NULL
      AND jp.domain IS NOT NULL
      AND COALESCE(jp.status, '') <> 'ignored'
      AND (jp.loc_country IS NULL OR jp.loc_country <> 'foreign')
      AND NOT EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
          'cgsfederal','accenture federal','booz allen','mantech','saic','caci','prosidian',
          'guidehouse','gdit','leidos','northrop grumman','parsons federal','serco federal',
          'deloitte federal','parsons','invisible agency','cermaticom','jobs for humanity',
          'devoteam','canonical','nxp semiconductors','relx','bosch group','about you se',
          'sixt','scalablegmbh'
        ]::text[]) AS blocked(match)
        WHERE LOWER(COALESCE(c.company_name, '')) LIKE '%' || blocked.match || '%'
      )
      AND (
        (jp.loc_country IN ('US', 'United States', 'USA') AND (
          jp.loc_state IS NULL OR UPPER(jp.loc_state) IN (
            'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD',
            'MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC',
            'SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC','PR','VI','GU','AS','MP'
          )
        ))
        OR (jp.loc_country IS NULL AND UPPER(COALESCE(jp.loc_state, '')) IN (
          'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD',
          'MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC',
          'SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC','PR','VI','GU','AS','MP'
        ))
        OR (jp.loc_country = 'unknown' AND LOWER(COALESCE(jp.ingestion_source, '')) IN ('greenhouse','lever','ashby'))
        OR (jp.loc_country = 'unknown' AND LOWER(COALESCE(jp.ingestion_source, '')) = 'workday'
            AND LOWER(COALESCE(jp.workplace_type, '')) = 'remote')
      )
  ), scoped AS (
    SELECT e.*, 'all'::text AS scope_domain FROM eligible e
    UNION ALL
    SELECT e.*, e.domain::text AS scope_domain FROM eligible e
  ), active AS (
    SELECT *
    FROM scoped
    WHERE CASE
      WHEN day >= current_date THEN is_public
      ELSE first_observed_date <= day AND COALESCE(last_observed_date, day) >= day
    END
  ), coverage AS (
    SELECT company_id, scope_domain, MIN(first_observed_date) AS coverage_started_at
    FROM scoped
    WHERE first_observed_date <= day
    GROUP BY company_id, scope_domain
  ), active_metrics AS (
    SELECT
      company_id,
      scope_domain,
      COUNT(*)::integer AS active_source_postings,
      COUNT(DISTINCT opportunity_id)::integer AS active_opportunities,
      COUNT(DISTINCT opportunity_id) FILTER (
        WHERE opportunity_first_observed_date BETWEEN day - 6 AND day
      )::integer AS new_opportunities_7d,
      ROUND(100.0 * COUNT(*) FILTER (WHERE salary_disclosed) / NULLIF(COUNT(*), 0), 1) AS salary_transparency_pct
    FROM active
    GROUP BY company_id, scope_domain
  ), closed_metrics AS (
    SELECT
      company_id,
      scope_domain,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY lifetime_days) FILTER (
        WHERE lifetime_days >= 2 AND lifetime_days <= 365
      ) AS median_closed_lifetime_days
    FROM scoped
    WHERE last_observed_date BETWEEN day - 89 AND day
      AND status = 'expired'
    GROUP BY company_id, scope_domain
    HAVING COUNT(*) FILTER (WHERE lifetime_days >= 2 AND lifetime_days <= 365) >= 5
  ), disappeared AS (
    SELECT
      company_id,
      scope_domain,
      COUNT(DISTINCT opportunity_id)::integer AS disappeared_opportunities_7d
    FROM scoped
    WHERE opportunity_last_observed_date BETWEEN day - 6 AND day
      AND CASE WHEN day >= current_date THEN NOT opportunity_is_currently_public ELSE true END
    GROUP BY company_id, scope_domain
  ), reposts AS (
    SELECT
      s.company_id,
      s.scope_domain,
      COUNT(DISTINCT r.event_id) FILTER (WHERE r.signal_class = 'individual_repost_signal')::integer
        AS individual_repost_signals_30d,
      COUNT(DISTINCT r.event_id) FILTER (WHERE r.signal_class = 'bulk_ats_date_refresh')::integer
        AS bulk_refresh_events_30d
    FROM scoped s
    JOIN mv_repost_events_classified r ON r.job_id = s.job_id
    WHERE r.observed_at::date BETWEEN day - 29 AND day
    GROUP BY s.company_id, s.scope_domain
  ), role_counts AS (
    SELECT company_id, scope_domain, role_category, COUNT(DISTINCT opportunity_id)::integer AS n
    FROM active
    WHERE role_category IS NOT NULL
    GROUP BY company_id, scope_domain, role_category
  ), ranked_roles AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY company_id, scope_domain ORDER BY n DESC, role_category) AS rank
    FROM role_counts
  ), role_mix AS (
    SELECT
      company_id,
      scope_domain,
      jsonb_agg(jsonb_build_object('role', role_category, 'count', n) ORDER BY n DESC, role_category) AS roles
    FROM ranked_roles
    WHERE rank <= 5
    GROUP BY company_id, scope_domain
  )
  INSERT INTO company_radar_daily (
    snapshot_date, company_id, domain, active_opportunities, active_source_postings,
    new_opportunities_7d, disappeared_opportunities_7d, salary_transparency_pct,
    median_closed_lifetime_days, individual_repost_signals_30d, bulk_refresh_events_30d,
    coverage_started_at, role_mix, snapshot_source, captured_at
  )
  SELECT
    day,
    cv.company_id,
    cv.scope_domain,
    COALESCE(a.active_opportunities, 0),
    COALESCE(a.active_source_postings, 0),
    COALESCE(a.new_opportunities_7d, 0),
    COALESCE(d.disappeared_opportunities_7d, 0),
    a.salary_transparency_pct,
    ROUND(cm.median_closed_lifetime_days::numeric, 1),
    COALESCE(r.individual_repost_signals_30d, 0),
    COALESCE(r.bulk_refresh_events_30d, 0),
    cv.coverage_started_at,
    COALESCE(rm.roles, '[]'::jsonb),
    CASE WHEN day < current_date THEN 'archive_reconstruction' ELSE 'live' END,
    now()
  FROM coverage cv
  LEFT JOIN active_metrics a USING (company_id, scope_domain)
  LEFT JOIN closed_metrics cm USING (company_id, scope_domain)
  LEFT JOIN disappeared d USING (company_id, scope_domain)
  LEFT JOIN reposts r USING (company_id, scope_domain)
  LEFT JOIN role_mix rm USING (company_id, scope_domain);

  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected;
END $$;

CREATE OR REPLACE FUNCTION ensure_company_radar_history(history_days integer DEFAULT 45)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
  d date;
  affected integer := 0;
  observed_days integer;
BEGIN
  history_days := GREATEST(31, LEAST(history_days, 120));
  SELECT COUNT(DISTINCT snapshot_date) INTO observed_days FROM company_radar_daily;

  IF observed_days < 31 THEN
    FOR d IN SELECT generate_series(current_date - (history_days - 1), current_date, interval '1 day')::date
    LOOP
      affected := affected + refresh_company_radar_snapshot(d);
    END LOOP;
  ELSE
    affected := refresh_company_radar_snapshot(current_date);
  END IF;
  RETURN affected;
END $$;

CREATE OR REPLACE FUNCTION generate_company_radar_alerts(day date DEFAULT current_date)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
  affected integer;
BEGIN
  WITH current_rows AS (
    SELECT * FROM company_radar_daily WHERE snapshot_date = day AND domain = 'all'
  ), prior_rows AS (
    SELECT * FROM company_radar_daily WHERE snapshot_date = day - 7 AND domain = 'all'
  ), signals AS (
    SELECT
      f.user_id,
      c.company_id,
      c.domain,
      'hiring_surge'::text AS alert_type,
      'Hiring accelerated'::text AS title,
      format('%s active opportunities, up %s in 7 days.', c.active_opportunities,
        c.active_opportunities - p.active_opportunities) AS detail,
      jsonb_build_object('active', c.active_opportunities, 'prior', p.active_opportunities) AS evidence
    FROM current_rows c
    JOIN prior_rows p USING (company_id, domain)
    JOIN user_followed_companies f ON f.company_id = c.company_id AND f.alert_frequency <> 'none'
    WHERE c.coverage_started_at <= day - 14
      AND p.active_opportunities >= 3
      AND c.active_opportunities - p.active_opportunities >= 3
      AND c.active_opportunities >= CEIL(p.active_opportunities * 1.25)

    UNION ALL

    SELECT
      f.user_id, c.company_id, c.domain, 'fresh_roles', 'Fresh roles appeared',
      format('%s opportunities first appeared in the last 7 days.', c.new_opportunities_7d),
      jsonb_build_object('new_7d', c.new_opportunities_7d, 'active', c.active_opportunities)
    FROM current_rows c
    JOIN user_followed_companies f ON f.company_id = c.company_id AND f.alert_frequency <> 'none'
    WHERE c.coverage_started_at <= day - 7 AND c.new_opportunities_7d >= 5

    UNION ALL

    SELECT
      f.user_id, c.company_id, c.domain, 'repost_watch', 'Repeated requisitions detected',
      format('%s individual repost signals in 30 days. Bulk ATS refreshes were excluded.', c.individual_repost_signals_30d),
      jsonb_build_object('individual_reposts_30d', c.individual_repost_signals_30d)
    FROM current_rows c
    JOIN user_followed_companies f ON f.company_id = c.company_id AND f.alert_frequency <> 'none'
    WHERE c.individual_repost_signals_30d >= 2
  )
  INSERT INTO company_radar_alerts (
    user_id, company_id, domain, alert_type, signal_date, title, detail, evidence
  )
  SELECT user_id, company_id, domain, alert_type, day, title, detail, evidence
  FROM signals
  ON CONFLICT (user_id, company_id, domain, alert_type, signal_date) DO NOTHING;

  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected;
END $$;

COMMENT ON TABLE company_radar_daily IS
  'Company Radar daily observations. Archive rows are reconstructed from retained posting lifetimes; live rows are exact published-feed observations.';
COMMENT ON COLUMN company_radar_daily.individual_repost_signals_30d IS
  'Verified role-level disappear/reappear events; excludes crawler churn and ATS-wide refresh cohorts.';
COMMENT ON TABLE company_research_events IS
  'Externally sourced company evidence. AI-written summaries must remain traceable to source_url/evidence.';
