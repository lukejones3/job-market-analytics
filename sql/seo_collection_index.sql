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
