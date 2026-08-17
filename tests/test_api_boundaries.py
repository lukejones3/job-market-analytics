from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import psycopg2

from python.api import (
    MOBILE_AUTH_CALLBACK,
    _billing_portal_return_url,
    _cursor_value,
    _legacy_credential_is_expired,
    _normalize_mobile_auth_callback,
    _set_public_query_timeout,
    app,
    outreach_fresh_matches,
    ttl_payload_cache,
)


def test_fresh_magic_link_renews_an_expired_account_credential() -> None:
    expired = datetime.now(timezone.utc) - timedelta(days=1)

    assert not _legacy_credential_is_expired(
        legacy_link=False,
        credential_expires_at=expired,
    )


def test_legacy_credential_link_still_honors_credential_expiry() -> None:
    now = datetime.now(timezone.utc)

    assert _legacy_credential_is_expired(
        legacy_link=True,
        credential_expires_at=now - timedelta(seconds=1),
        now=now,
    )
    assert not _legacy_credential_is_expired(
        legacy_link=True,
        credential_expires_at=now + timedelta(seconds=1),
        now=now,
    )


def test_magic_link_rotation_reads_tuple_and_real_dict_cursor_rows() -> None:
    assert _cursor_value({"api_key_hash": "test-value"}, "api_key_hash") == "test-value"  # pragma: allowlist secret
    assert _cursor_value(("test-value",), "api_key_hash") == "test-value"  # pragma: allowlist secret
    assert _cursor_value(None, "api_key_hash") is None


def test_billing_portal_return_url_is_server_owned() -> None:
    with patch.dict("os.environ", {"LANDER_BASE_URL": "https://www.landerjob.com/"}):
        assert _billing_portal_return_url() == "https://www.landerjob.com/settings"


def test_public_query_timeout_is_transaction_local() -> None:
    cursor = Mock()

    _set_public_query_timeout(cursor)

    cursor.execute.assert_called_once_with(
        "SELECT set_config('statement_timeout', %s, true)",
        ("10000",),
    )


def test_mobile_callback_defaults_to_the_installed_app() -> None:
    assert _normalize_mobile_auth_callback(None) == MOBILE_AUTH_CALLBACK
    assert _normalize_mobile_auth_callback(MOBILE_AUTH_CALLBACK) == MOBILE_AUTH_CALLBACK


def test_mobile_callback_rejects_arbitrary_allowed_schemes() -> None:
    assert _normalize_mobile_auth_callback("lander://attacker/collect") is None
    assert _normalize_mobile_auth_callback("exp://attacker.example/--/auth/verify") is None


def test_mobile_callback_requires_an_exact_operator_allow_list_entry() -> None:
    allowed = "exp://127.0.0.1:8081/--/auth/verify"
    with patch.dict("os.environ", {"LANDER_MOBILE_AUTH_CALLBACK_ALLOWLIST": allowed}):
        assert _normalize_mobile_auth_callback(allowed) == allowed
        assert _normalize_mobile_auth_callback(f"{allowed}/other") is None


def test_api_docs_are_private_by_default() -> None:
    assert app.docs_url is None
    assert app.openapi_url is None


def test_health_and_public_endpoints_support_head() -> None:
    head_paths = {
        route.path
        for route in app.routes
        if "HEAD" in getattr(route, "methods", set())
    }
    assert "/health" in head_paths
    assert "/v1/public/market" in head_paths
    assert "/v1/public/insights/{insight_slug}" in head_paths
    assert "/v1/public/answers/{answer_slug}" in head_paths


def test_public_payload_cache_populates_shared_storage_once() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [None, None]
    calls = 0

    @ttl_payload_cache(300)
    def endpoint(*, conn):
        nonlocal calls
        calls += 1
        return {"generated_at": "2026-08-08T00:00:00Z", "value": 1}

    first = endpoint(conn=conn)
    second = endpoint(conn=conn)

    assert first == second
    assert calls == 1
    conn.commit.assert_called_once()


def test_public_payload_cache_replaces_a_closed_connection() -> None:
    broken = MagicMock()
    broken.cursor.side_effect = psycopg2.OperationalError("SSL connection has been closed unexpectedly")
    broken.rollback.side_effect = psycopg2.InterfaceError("connection already closed")
    healthy = MagicMock()
    healthy_cursor = MagicMock()
    healthy.cursor.return_value.__enter__.return_value = healthy_cursor
    healthy_cursor.fetchone.side_effect = [None, None]

    @ttl_payload_cache(300)
    def endpoint(*, conn):
        assert conn is healthy
        return {"ok": True}

    with (
        patch("python.api._get_healthy_pool_connection", return_value=healthy),
        patch("python.api._return_pool_connection") as return_connection,
    ):
        assert endpoint(conn=broken) == {"ok": True}

    return_connection.assert_called_once_with(healthy)


def test_outreach_feed_preserves_the_evidence_needed_for_strict_career_review() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    posted = datetime.now(timezone.utc) - timedelta(days=1)
    cursor.fetchall.return_value = [{
        "job_id": "job-1",
        "title": "Sales Data Analyst",
        "company_id": "company-1",
        "company_name": "Example",
        "job_url": "https://example.com/careers/job-1",
        "posted_at": posted,
        "workplace_type": "Remote",
        "employment_type": "Full-time",
        "experience_level": "Mid",
        "salary_min": 76500.0,
        "salary_max": 103500.0,
        "salary_period": "year",
        "country": "US",
        "city": "",
        "state": "",
        "source": "workday",
        "description": "Build production SQL and Python pipelines. Remote in the United States.",
        "location_rank": 3,
        "role_rank": 4,
        "known_recruiters": [],
    }]
    request = MagicMock()
    request.headers = {"X-Lander-Internal-Key": "test-key"}

    with patch("python.api.LANDER_INTERNAL_API_KEY", "test-key"):
        payload = outreach_fresh_matches(request=request, days=7, limit=30, conn=conn)

    result = payload["results"][0]
    assert result["description_excerpt"].startswith("Build production SQL")
    assert result["employment_type"] == "Full-time"
    assert result["salary_min"] == 76500.0
    assert result["salary_max"] == 103500.0
    assert "open_verified_at" not in result, "the feed must not impersonate a live employer-page check"
    query = cursor.execute.call_args.args[0]
    assert "jp.loc_country" in query
    assert "salary_max_annual >= 80000" in query
    assert "forward.?deployed" in query
    assert "ai evaluation" in query
    assert "semantic search" in query
    assert "talent intelligence" in query
    assert "developer productivity" in query
    assert "tacoma" not in query.lower()
