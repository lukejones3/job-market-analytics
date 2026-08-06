CREATE TABLE IF NOT EXISTS public.seo_role_location_index (
  role_category text NOT NULL,
  location_slug text NOT NULL REFERENCES public.seo_locations(location_slug),
  job_count integer NOT NULL CHECK (job_count >= 0),
  refreshed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (role_category, location_slug)
);

CREATE INDEX IF NOT EXISTS seo_role_location_index_location_count
  ON public.seo_role_location_index (location_slug, job_count DESC);
