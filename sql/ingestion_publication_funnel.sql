CREATE OR REPLACE VIEW ingestion_publication_funnel AS
WITH scoped AS (
  SELECT
    jp.*,
    c.company_name,
    (
      jp.status = 'raw'
      AND jp.data_tier = 1
      AND jp.domain IS NOT NULL
      AND jp.role_id IS NOT NULL
      AND c.company_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM unnest(ARRAY[
          'cgsfederal','accenture federal','booz allen','mantech','saic','caci',
          'prosidian','guidehouse','gdit','leidos','northrop grumman','parsons federal',
          'serco federal','deloitte federal','parsons','invisible agency','cermaticom',
          'jobs for humanity','devoteam','canonical','nxp semiconductors','relx',
          'bosch group','about you se','sixt','scalablegmbh'
        ]::text[]) blocked(match)
        WHERE LOWER(c.company_name) LIKE '%' || blocked.match || '%'
      )
      AND (
        (jp.loc_country IN ('US','United States','USA') AND
          (jp.loc_state IS NULL OR UPPER(jp.loc_state) = ANY(ARRAY[
            'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS',
            'KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY',
            'NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV',
            'WI','WY','DC','PR','VI','GU','AS','MP'
          ]::text[])))
        OR (jp.loc_country IS NULL AND UPPER(COALESCE(jp.loc_state,'')) = ANY(ARRAY[
          'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS',
          'KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY',
          'NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV',
          'WI','WY','DC','PR','VI','GU','AS','MP'
        ]::text[]))
        OR (jp.loc_country = 'unknown' AND LOWER(COALESCE(jp.ingestion_source,'')) IN ('greenhouse','lever','ashby'))
        OR (jp.loc_country = 'unknown' AND LOWER(COALESCE(jp.ingestion_source,'')) = 'workday'
          AND LOWER(COALESCE(jp.workplace_type,'')) = 'remote')
      )
    ) AS frontend_visible
  FROM job_postings jp
  LEFT JOIN companies c ON c.company_id = jp.company_id
)
SELECT
  ingestion_source,
  COUNT(*) FILTER (WHERE status = 'raw')::bigint AS active_rows,
  COUNT(*) FILTER (WHERE status = 'raw' AND data_tier = 1)::bigint AS tier1_rows,
  COUNT(*) FILTER (WHERE status = 'raw' AND data_tier = 1 AND (
    loc_country IN ('US','United States','USA') OR
    (loc_country IS NULL AND loc_state IS NOT NULL) OR
    (loc_country = 'unknown' AND LOWER(COALESCE(ingestion_source,'')) IN ('greenhouse','lever','ashby')) OR
    (loc_country = 'unknown' AND LOWER(COALESCE(ingestion_source,'')) = 'workday'
      AND LOWER(COALESCE(workplace_type,'')) = 'remote')
  ))::bigint AS us_candidate_rows,
  COUNT(*) FILTER (WHERE status = 'raw' AND data_tier = 1 AND domain IS NOT NULL)::bigint AS classified_rows,
  COUNT(*) FILTER (WHERE status = 'raw' AND data_tier = 1 AND domain IS NULL)::bigint AS unresolved_domain_rows,
  COUNT(*) FILTER (WHERE frontend_visible)::bigint AS publishable_rows,
  MAX(last_seen_at) AS latest_seen_at
FROM scoped
GROUP BY ingestion_source;
