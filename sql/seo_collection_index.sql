CREATE TABLE IF NOT EXISTS public.seo_role_location_index (
  role_category text NOT NULL,
  location_slug text NOT NULL REFERENCES public.seo_locations(location_slug),
  job_count integer NOT NULL CHECK (job_count >= 0),
  refreshed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (role_category, location_slug)
);

CREATE INDEX IF NOT EXISTS seo_role_location_index_location_count
  ON public.seo_role_location_index (location_slug, job_count DESC);

CREATE TABLE IF NOT EXISTS public.seo_company_index (
  company_id text PRIMARY KEY,
  company_slug text NOT NULL UNIQUE,
  company_name text NOT NULL,
  sector text,
  job_count integer NOT NULL,
  median_salary double precision,
  p25_salary double precision,
  p75_salary double precision,
  avg_comp_annual double precision,
  avg_ghost double precision,
  median_ghost double precision,
  salary_disclosure_rate double precision,
  refreshed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS seo_company_index_count ON public.seo_company_index(job_count DESC);

CREATE TABLE IF NOT EXISTS public.seo_skill_index (
  skill_id text PRIMARY KEY,
  skill_slug text NOT NULL UNIQUE,
  skill_name text NOT NULL,
  job_count integer NOT NULL,
  median_salary double precision,
  p25_salary double precision,
  p75_salary double precision,
  market_median_salary double precision,
  refreshed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS seo_skill_index_count ON public.seo_skill_index(job_count DESC);

CREATE TABLE IF NOT EXISTS public.seo_crawl_events (
  crawled_at timestamptz NOT NULL DEFAULT now(),
  path text NOT NULL,
  crawler text NOT NULL,
  referrer text
);
CREATE INDEX IF NOT EXISTS seo_crawl_events_time ON public.seo_crawl_events(crawled_at DESC);

CREATE TABLE IF NOT EXISTS public.seo_indexing_queue (
  job_id text NOT NULL,
  url text NOT NULL,
  notification_type text NOT NULL CHECK (notification_type IN ('URL_UPDATED','URL_DELETED')),
  queued_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  last_error text,
  PRIMARY KEY(job_id, notification_type, queued_at)
);
ALTER TABLE public.seo_indexing_queue
  ADD COLUMN IF NOT EXISTS indexnow_sent_at timestamptz,
  ADD COLUMN IF NOT EXISTS indexnow_attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS indexnow_last_error text;
CREATE INDEX IF NOT EXISTS seo_indexing_queue_pending ON public.seo_indexing_queue(queued_at) WHERE sent_at IS NULL;
CREATE INDEX IF NOT EXISTS seo_indexing_queue_indexnow_pending
  ON public.seo_indexing_queue(queued_at) WHERE indexnow_sent_at IS NULL;
