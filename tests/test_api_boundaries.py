import psycopg2
from unittest.mock import MagicMock, Mock, patch

from python.api import (
    MOBILE_AUTH_CALLBACK,
    _billing_portal_return_url,
    _normalize_mobile_auth_callback,
    _set_public_query_timeout,
    app,
    ttl_payload_cache,
)


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
