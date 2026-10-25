BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS public.product_lifecycle_events (
    user_id text NOT NULL REFERENCES public.api_keys(key_id) ON DELETE CASCADE,
    event_name text NOT NULL CHECK(event_name IN
      ('authenticated','first_search','resume_matched','applied','outcome_recorded','upgraded')),
    dedupe_key text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id,event_name,dedupe_key)
);
CREATE INDEX IF NOT EXISTS product_lifecycle_events_recent ON public.product_lifecycle_events(occurred_at,event_name);
COMMENT ON TABLE public.product_lifecycle_events IS
  'Authenticated feature milestones; no query text, document contents, URLs, IPs or emails. Retain 90 days.';
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='lander_web') THEN
    GRANT SELECT,INSERT,DELETE ON public.product_lifecycle_events TO lander_web;
    GRANT DELETE ON public.auth_magic_tokens,public.api_usage,public.company_radar_alert_deliveries TO lander_web;
    GRANT DELETE ON public.outreach_recipients TO lander_web;
  END IF;
END $$;
COMMIT;
