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
COMMIT;
