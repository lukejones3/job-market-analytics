-- Apply before deploying the corresponding web release. No historical content
-- modification dates are invented: existing rows keep NULL until a real change.
SET lock_timeout = '5s';
ALTER TABLE public.job_postings ADD COLUMN IF NOT EXISTS content_updated_at timestamptz;

CREATE OR REPLACE FUNCTION public.track_job_content_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW.content_updated_at := now();
  ELSIF ROW(NEW.description_text,NEW.role_id,NEW.company_id,NEW.job_url,
            NEW.posted_date,NEW.salary_min_annual,NEW.salary_max_annual,
            NEW.workplace_type,NEW.employment_type,NEW.experience_level,
            NEW.loc_city,NEW.loc_state,NEW.loc_country,NEW.is_public)
        IS DISTINCT FROM
        ROW(OLD.description_text,OLD.role_id,OLD.company_id,OLD.job_url,
            OLD.posted_date,OLD.salary_min_annual,OLD.salary_max_annual,
            OLD.workplace_type,OLD.employment_type,OLD.experience_level,
            OLD.loc_city,OLD.loc_state,OLD.loc_country,OLD.is_public) THEN
    NEW.content_updated_at := now();
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_job_content_update ON public.job_postings;
CREATE TRIGGER trg_job_content_update BEFORE INSERT OR UPDATE ON public.job_postings
FOR EACH ROW EXECUTE FUNCTION public.track_job_content_update();
CREATE INDEX IF NOT EXISTS seo_indexing_queue_url_latest ON public.seo_indexing_queue(url,queued_at DESC);
RESET lock_timeout;
