CREATE TABLE IF NOT EXISTS public.auth_magic_tokens (
    token_hash TEXT PRIMARY KEY,
    key_id TEXT NOT NULL REFERENCES public.api_keys(key_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '20 minutes',
    used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_auth_magic_tokens_key_created
    ON public.auth_magic_tokens(key_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_auth_magic_tokens_expiry_unused
    ON public.auth_magic_tokens(expires_at)
    WHERE used_at IS NULL;

COMMENT ON TABLE public.auth_magic_tokens IS
    'Single-use, short-lived email sign-in challenges. These are deliberately separate from API credentials.';
