"""Canonical HTTP cache allow-list for the public Lander API."""

from typing import Optional


PUBLIC_CACHE_POLICIES = {
    "/v1/public/market": "public, max-age=60, s-maxage=300, stale-while-revalidate=60",
}
PUBLIC_INSIGHT_CACHE_CONTROL = "public, max-age=60, s-maxage=900, stale-while-revalidate=120"


def public_cache_control(path: str) -> Optional[str]:
    if path.startswith("/v1/public/insights/") or path.startswith("/v1/public/answers/"):
        return PUBLIC_INSIGHT_CACHE_CONTROL
    return PUBLIC_CACHE_POLICIES.get(path)
