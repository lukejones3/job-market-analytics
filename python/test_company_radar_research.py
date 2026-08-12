"""Dependency-light regression tests for Company Radar research grounding."""

from company_radar_research import (
    Source,
    classify_event,
    deterministic_brief,
    extract_response_text,
    monthly_usage,
    normalize_sources,
)
from company_radar_notify import render_digest


def test_classification_prefers_explicit_evidence():
    assert classify_event("Acme announces a 12% workforce reduction") == "layoff"
    assert classify_event("Acme raises Series C funding") == "funding"
    assert classify_event("Acme is hiring 200 engineers") == "hiring"
    assert classify_event("Acme releases a new blue logo") == "other"


def test_normalize_sources_rejects_duplicates_and_non_http_links():
    payload = {
        "news": [
            {"title": "Acme expands", "link": "https://example.com/a", "snippet": "Acme opens a new office.", "date": "2 days ago"},
            {"title": "Duplicate", "link": "https://example.com/a", "snippet": "Same URL"},
            {"title": "Bad", "link": "javascript:alert(1)", "snippet": "Unsafe"},
        ]
    }
    rows = normalize_sources(payload)
    assert len(rows) == 1
    assert rows[0].domain == "example.com"


def test_fallback_summary_never_adds_unseen_claims():
    source = Source(
        title="Acme reports quarterly results",
        url="https://example.com/results",
        snippet="Revenue increased during the quarter.",
        published_at=None,
        domain="example.com",
    )
    brief = deterministic_brief(source)
    assert brief["headline"] == source.title
    assert brief["summary"] == source.snippet
    assert brief["event_type"] == "earnings"


def test_extract_responses_output_text():
    payload = {
        "output": [
            {"content": [{"type": "output_text", "text": '{"briefs":'}, {"type": "refusal", "refusal": ""}]},
            {"content": [{"type": "output_text", "text": "[]}"}]},
        ]
    }
    assert extract_response_text(payload) == '{"briefs":[]}'


def test_monthly_usage_reads_real_dict_cursor_shape():
    class Cursor:
        def execute(self, sql, params):
            assert "AS request_count" in sql
            assert params == ("serper",)

        def fetchone(self):
            return {"request_count": 17}

    assert monthly_usage(Cursor(), "serper") == 17


def test_digest_escapes_external_company_text():
    subject, body = render_digest(
        [{
            "company_name": "<Acme>", "title": "Hiring <surge>", "detail": "Five & growing",
            "company_slug": "acme", "alert_id": 1, "alert_type": "hiring_surge", "signal_date": "2026-08-08",
        }],
        "daily",
    )
    assert "<Acme>" not in body
    assert "&lt;Acme&gt;" in body
    assert "Five &amp; growing" in body
    assert "1 signal" in subject


if __name__ == "__main__":
    test_classification_prefers_explicit_evidence()
    test_normalize_sources_rejects_duplicates_and_non_http_links()
    test_fallback_summary_never_adds_unseen_claims()
    test_extract_responses_output_text()
    test_digest_escapes_external_company_text()
    print("company radar research tests passed")
