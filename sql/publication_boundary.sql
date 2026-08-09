-- Atomic public-feed boundary. Ingestion and enrichment may freely mutate raw
-- rows; consumers see only one canonical representative from the last snapshot
-- that passed publication checks.
BEGIN;

ALTER TABLE job_postings
  ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS canonical_opportunity_id TEXT,
  ADD COLUMN IF NOT EXISTS location_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS hiring_organization TEXT,
  ADD COLUMN IF NOT EXISTS valid_through TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS direct_apply BOOLEAN,
  ADD COLUMN IF NOT EXISTS source_quality_status TEXT NOT NULL DEFAULT 'active';

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

-- Database-owned company exclusions replace drift-prone copies in every
-- consumer. Substring matches are intentional for branded federal divisions.
CREATE TABLE IF NOT EXISTS publication_company_exclusions (
  match_text TEXT PRIMARY KEY,
  match_type TEXT NOT NULL DEFAULT 'substring'
    CHECK (match_type IN ('exact', 'substring')),
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO publication_company_exclusions (match_text, match_type, reason)
VALUES
  ('cgsfederal', 'substring', 'federal/staffing exclusion'),
  ('accenture federal', 'substring', 'federal/staffing exclusion'),
  ('booz allen', 'substring', 'federal/staffing exclusion'),
  ('mantech', 'substring', 'federal/staffing exclusion'),
  ('saic', 'substring', 'federal/staffing exclusion'),
  ('caci', 'substring', 'federal/staffing exclusion'),
  ('prosidian', 'substring', 'federal/staffing exclusion'),
  ('guidehouse', 'substring', 'federal/staffing exclusion'),
  ('gdit', 'substring', 'federal/staffing exclusion'),
  ('leidos', 'substring', 'federal/staffing exclusion'),
  ('northrop grumman', 'substring', 'federal/staffing exclusion'),
  ('parsons federal', 'substring', 'federal/staffing exclusion'),
  ('serco federal', 'substring', 'federal/staffing exclusion'),
  ('deloitte federal', 'substring', 'federal/staffing exclusion'),
  ('parsons', 'substring', 'federal/staffing exclusion'),
  ('invisible agency', 'substring', 'aggregator exclusion'),
  ('cermaticom', 'substring', 'wrong-market exclusion'),
  ('jobs for humanity', 'substring', 'aggregator exclusion'),
  ('devoteam', 'substring', 'wrong-market exclusion'),
  ('canonical', 'substring', 'wrong-market exclusion'),
  ('nxp semiconductors', 'substring', 'wrong-market exclusion'),
  ('relx', 'substring', 'wrong-market exclusion'),
  ('bosch group', 'substring', 'wrong-market exclusion'),
  ('about you se', 'substring', 'wrong-market exclusion'),
  ('sixt', 'substring', 'wrong-market exclusion'),
  ('scalablegmbh', 'substring', 'wrong-market exclusion'),
  ('dept®', 'exact', 'wrong-market exclusion'),
  ('voodoo', 'exact', 'wrong-market exclusion'),
  ('truelogic', 'exact', 'wrong-market exclusion'),
  ('lendable', 'exact', 'wrong-market exclusion'),
  ('lightspeedhq', 'exact', 'wrong-market exclusion'),
  ('deliveroo', 'exact', 'wrong-market exclusion'),
  ('ing', 'exact', 'wrong-market exclusion'),
  ('hopper', 'exact', 'wrong-market exclusion'),
  ('elliptic', 'exact', 'wrong-market exclusion'),
  ('docebo', 'exact', 'wrong-market exclusion'),
  ('heidihealth.com.au', 'exact', 'wrong-market exclusion')
ON CONFLICT (match_text) DO UPDATE
SET match_type = EXCLUDED.match_type,
    reason = EXCLUDED.reason;

-- Candidate eligibility is recalculated from mutable ingestion/enrichment
-- state. Only the publisher reads this view. The public-facing view below is a
-- stable snapshot of rows that survived the last successful publication.
-- The legacy ATS exception preserves plausible existing unknown-country rows
-- while rejecting explicit foreign evidence. Career-host/custom sources never
-- get that exception: they must carry structured US evidence at ingestion.
CREATE OR REPLACE VIEW public.vw_lander_publication_candidates AS
WITH eligible AS (
  SELECT
    jp.job_id,
    COALESCE(NULLIF(jp.canonical_opportunity_id, ''), jp.job_id) AS canonical_opportunity_id,
    row_number() OVER (
      PARTITION BY COALESCE(NULLIF(jp.canonical_opportunity_id, ''), jp.job_id)
      ORDER BY
        CASE COALESCE(jp.ingestion_source, jp.source, '')
          WHEN 'employer_feed' THEN 0
          WHEN 'career_site' THEN 1
          WHEN 'oracle_cloud' THEN 2
          WHEN 'greenhouse' THEN 3
          WHEN 'ashby' THEN 4
          WHEN 'lever' THEN 5
          WHEN 'workday' THEN 6
          ELSE 7
        END,
        length(COALESCE(jp.description_text, '')) DESC,
        jp.posted_date DESC NULLS LAST,
        jp.last_seen_at DESC NULLS LAST,
        jp.job_id
    ) AS representative_rank
  FROM job_postings jp
  JOIN companies c ON c.company_id = jp.company_id
  JOIN roles r ON r.role_id = jp.role_id
  LEFT JOIN locations l ON l.location_id = jp.location_id
  WHERE jp.status = 'raw'
    AND jp.data_tier = 1
    AND COALESCE(jp.source, '') <> 'adzuna'
    AND jp.scope_status IN ('accepted_core', 'accepted_evidence')
    AND jp.company_id IS NOT NULL
    AND jp.role_id IS NOT NULL
    AND jp.domain IS NOT NULL
    AND jp.role_category IS NOT NULL
    AND jp.experience_level IS NOT NULL
    AND jp.embedding IS NOT NULL
    AND length(COALESCE(jp.description_text, '')) >= 100
    AND COALESCE(jp.source_quality_status, 'active') = 'active'
    AND (jp.valid_through IS NULL OR jp.valid_through >= now())
    AND NOT EXISTS (
      SELECT 1
      FROM publication_company_exclusions pce
      WHERE CASE pce.match_type
        WHEN 'exact' THEN lower(btrim(c.company_name)) = lower(pce.match_text)
        ELSE lower(c.company_name) LIKE '%' || lower(pce.match_text) || '%'
      END
    )
    AND (
      (
        lower(COALESCE(jp.loc_country, '')) IN ('us', 'united states', 'usa')
        AND (
          NULLIF(btrim(jp.loc_state), '') IS NULL
          OR upper(jp.loc_state) = ANY (ARRAY[
            'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME',
            'MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA',
            'RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC','PR','VI','GU','AS','MP'
          ])
        )
      )
      OR (
        jp.loc_country IS NULL
        AND upper(COALESCE(jp.loc_state, '')) = ANY (ARRAY[
          'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME',
          'MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA',
          'RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC','PR','VI','GU','AS','MP'
        ])
      )
      OR (
        lower(COALESCE(jp.loc_country, '')) = 'unknown'
        AND upper(COALESCE(jp.loc_state, '')) = ANY (ARRAY[
          'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME',
          'MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA',
          'RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC','PR','VI','GU','AS','MP'
        ])
      )
      OR (
        lower(COALESCE(jp.loc_country, '')) = 'unknown'
        AND lower(COALESCE(jp.ingestion_source, '')) IN ('greenhouse', 'lever', 'ashby')
        AND concat_ws(' ', r.role_name, l.location, jp.loc_city, jp.loc_state)
          !~* '\m(armenia|asia|australia|austria|baku|belgium|belgrade|berlin|brazil|brasil|canada|china|denmark|dublin|europe|france|germany|gdańsk|herzliya|india|ireland|israel|italy|japan|korea|latin america|london|málaga|mexico|netherlands|poland|portugal|singapore|spain|sweden|switzerland|tokyo|toronto|united kingdom|warsaw)\M'
      )
      OR (
        lower(COALESCE(jp.loc_country, '')) = 'unknown'
        AND lower(COALESCE(jp.ingestion_source, '')) = 'workday'
        AND lower(COALESCE(jp.workplace_type, '')) = 'remote'
        AND concat_ws(' ', r.role_name, l.location, jp.loc_city, jp.loc_state)
          !~* '\m(armenia|asia|australia|austria|baku|belgium|belgrade|berlin|brazil|brasil|canada|china|denmark|dublin|europe|france|germany|gdańsk|herzliya|india|ireland|israel|italy|japan|korea|latin america|london|málaga|mexico|netherlands|poland|portugal|singapore|spain|sweden|switzerland|tokyo|toronto|united kingdom|warsaw)\M'
      )
    )
)
SELECT job_id, canonical_opportunity_id
FROM eligible
WHERE representative_rank = 1;

COMMENT ON VIEW public.vw_lander_publication_candidates IS
  'Mutable canonical, company, content, lifecycle, and US-location candidate set read only by the atomic publisher.';

CREATE OR REPLACE VIEW public.vw_lander_visible_opportunities AS
SELECT
  jp.job_id,
  COALESCE(NULLIF(jp.canonical_opportunity_id, ''), jp.job_id) AS canonical_opportunity_id
FROM job_postings jp
WHERE jp.is_public = true;

COMMENT ON VIEW public.vw_lander_visible_opportunities IS
  'Stable public snapshot from the last successful publication; all product consumers join through this view.';

-- Safe first-deploy seed. Later changes happen only in publish_snapshot.py.
UPDATE job_postings jp
SET is_public = true,
    published_at = COALESCE(published_at, now())
WHERE NOT EXISTS (SELECT 1 FROM job_postings WHERE is_public = true)
  AND EXISTS (
    SELECT 1 FROM public.vw_lander_publication_candidates candidate
    WHERE candidate.job_id = jp.job_id
  );

COMMIT;
