BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS public.billing_customers (
  customer_id text PRIMARY KEY,
  key_id text NOT NULL REFERENCES public.api_keys(key_id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS billing_customers_key ON public.billing_customers(key_id);
CREATE TABLE IF NOT EXISTS public.billing_subscriptions (
  subscription_id text PRIMARY KEY,
  customer_id text NOT NULL REFERENCES public.billing_customers(customer_id),
  key_id text NOT NULL REFERENCES public.api_keys(key_id),
  status text NOT NULL,
  qualifies boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS billing_subscriptions_account ON public.billing_subscriptions(key_id,status);
CREATE TABLE IF NOT EXISTS public.billing_events (
  event_id text PRIMARY KEY,
  event_type text NOT NULL,
  stripe_created_at timestamptz NOT NULL,
  processed_at timestamptz NOT NULL
);
COMMIT;
