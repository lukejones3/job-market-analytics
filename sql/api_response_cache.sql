CREATE TABLE IF NOT EXISTS api_response_cache (
    cache_key text PRIMARY KEY,
    payload jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_response_cache_expires_at
    ON api_response_cache(expires_at);
