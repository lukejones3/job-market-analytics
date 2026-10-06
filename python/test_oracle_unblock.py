"""Tests for the Oracle Cloud unblock: validator, identity auto-review,
and api_recipe crawl dispatch.

Dependency-light like test_career_host_engine: no database, no network.
"""
from __future__ import annotations

import json

import pytest

import validate_ats_candidates as validators
from career_host_engine import (
    _host_source,
    _is_opaque_oracle_name,
    _oracle_effective_url,
    _oracle_listings_page,
    decide_oracle_review,
    extract_oracle_organizations,
)
from integrate_ats_candidates import _board_token, _oracle_careers_url


# ---------------------------------------------------------------------------
# Identity auto-review decisions
# ---------------------------------------------------------------------------

def test_opaque_names_are_site_tokens_not_employers() -> None:
    assert _is_opaque_oracle_name("CX_1001")
    assert _is_opaque_oracle_name("Cx 1001")
    assert _is_opaque_oracle_name("")
    assert not _is_opaque_oracle_name("Booz Allen Hamilton")


def test_review_resolves_when_site_identity_matches_candidate() -> None:
    decision, name, reason = decide_oracle_review("Acme, Inc.", ["Acme"])
    assert (decision, name, reason) == ("resolved", "Acme, Inc.", "identity_match")


def test_review_rejects_when_site_identity_mismatches() -> None:
    decision, _, reason = decide_oracle_review("Acme", ["Contoso Health"])
    assert decision == "rejected"
    assert reason == "identity_mismatch"


def test_review_waits_without_identity_evidence() -> None:
    decision, _, reason = decide_oracle_review("Acme", [])
    assert decision == "needs_review"
    assert reason == "no_identity_evidence"


def test_review_adopts_consistent_site_identity_for_opaque_seed() -> None:
    decision, name, reason = decide_oracle_review(
        "CX_1001", ["Acme", "Acme, Inc.", "Acme"])
    assert (decision, name, reason) == ("resolved", "Acme", "site_identity_adopted")


def test_review_waits_on_single_or_conflicting_site_identity() -> None:
    decision, _, reason = decide_oracle_review("CX_1001", ["Acme"])
    assert (decision, reason) == ("needs_review", "insufficient_identity_samples")
    decision, _, reason = decide_oracle_review("CX_1001", ["Acme", "Contoso"])
    assert (decision, reason) == ("needs_review", "multiple_organizations")


def test_extract_organizations_reads_payload_shapes() -> None:
    listings = [
        {"Title": "Data Engineer", "Organization": "Acme"},
        {"Title": "Analyst", "HiringOrganization": {"name": "Acme, Inc."}},
        {"Title": "Scientist"},
    ]
    assert extract_oracle_organizations(listings) == ["Acme", "Acme, Inc."]


# ---------------------------------------------------------------------------
# Oracle API probe shared by crawler / validator / reviewer
# ---------------------------------------------------------------------------

class _FakeResponse:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload
        self.headers: dict = {}
        self.content = json.dumps(payload).encode()

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.payload)


def test_listings_page_parses_requisition_collection() -> None:
    payload = {"items": [{"TotalJobsCount": 42, "requisitionList": [
        {"Id": "1", "Title": "Data Engineer"},
        {"Id": "2", "Title": "Data Scientist"},
    ]}]}
    batch, total = _oracle_listings_page(
        _FakeSession(payload), "https://acme.oraclecloud.com", "CX_1", 25, 0)
    assert total == 42
    assert [row["Id"] for row in batch] == ["1", "2"]


# ---------------------------------------------------------------------------
# ATS validator
# ---------------------------------------------------------------------------

def _oracle_payload(listings: list[dict]) -> dict:
    return {"items": [{"TotalJobsCount": len(listings), "requisitionList": listings}]}


def _patch_oracle_api(monkeypatch, payload: dict, status_code: int = 200) -> None:
    def fake_get(url, params=None, timeout=None, headers=None):
        assert "siteNumber=CX_1" in (params or {}).get("finder", "")
        response = _FakeResponse(payload)
        response.status_code = status_code
        return response

    monkeypatch.setattr(validators.requests, "get", fake_get)


_ROW = {"tenant": "CX_1", "server": "acme.fa.us2.oraclecloud.com", "company_name": "Acme"}


def test_validator_counts_us_and_target_with_location_gate(monkeypatch) -> None:
    _patch_oracle_api(monkeypatch, _oracle_payload([
        {"Title": "Senior Data Engineer", "PrimaryLocationCountry": "US",
         "PrimaryLocation": "Chicago, Illinois, United States"},
        {"Title": "Warehouse Associate", "PrimaryLocationCountry": "US",
         "PrimaryLocation": "Chicago, Illinois, United States"},
        {"Title": "Data Scientist", "PrimaryLocationCountry": "IN",
         "PrimaryLocation": "Bengaluru, India"},
    ]))
    server, us_jobs, target, status = validators.validate_oracle_cloud(_ROW)
    assert server == "acme.fa.us2.oraclecloud.com/CX_1"
    assert (us_jobs, target, status) == (2, 1, "active")


def test_validator_no_target_is_no_data_jobs_not_active(monkeypatch) -> None:
    _patch_oracle_api(monkeypatch, _oracle_payload([
        {"Title": "Warehouse Associate", "PrimaryLocationCountry": "US",
         "PrimaryLocation": "Chicago, Illinois, United States"},
    ]))
    _, us_jobs, target, status = validators.validate_oracle_cloud(_ROW)
    assert (us_jobs, target, status) == (1, 0, "no_data_jobs")


def test_validator_unreachable_on_http_error(monkeypatch) -> None:
    _patch_oracle_api(monkeypatch, {}, status_code=403)
    assert validators.validate_oracle_cloud(_ROW) == (None, 0, 0, "unreachable")


def test_validator_is_registered() -> None:
    assert validators.VALIDATORS["oracle_cloud"] is validators.validate_oracle_cloud


def test_candidate_locator_accepts_url_and_locator_forms() -> None:
    by_url = {"tenant": "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_9/jobs",
              "server": None}
    assert validators._oracle_candidate_locator(by_url) == (
        "https://acme.fa.us2.oraclecloud.com", "CX_9")
    by_locator = {"tenant": "CX_9", "server": "acme.fa.us2.oraclecloud.com/CX_9"}
    assert validators._oracle_candidate_locator(by_locator) == (
        "https://acme.fa.us2.oraclecloud.com", "CX_9")
    assert validators._oracle_candidate_locator({"tenant": "CX_9", "server": None}) is None


# ---------------------------------------------------------------------------
# api_recipe dispatch + integration routing
# ---------------------------------------------------------------------------

def test_api_recipe_oracle_dispatch_and_url() -> None:
    host = {
        "extraction_strategy": "api_recipe",
        "careers_url": "https://example.com/careers",
        "tenant_token": "CX_7",
        "api_recipe": {"platform": "oracle_cloud", "origin": "https://acme.oraclecloud.com", "site": "CX_7"},
    }
    assert _host_source(host) == "oracle_cloud"
    assert _oracle_effective_url(host) == (
        "https://acme.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_7")
    json_recipe = dict(host, api_recipe=json.dumps(host["api_recipe"]))
    assert _host_source(json_recipe) == "oracle_cloud"


def test_plain_hosts_keep_their_sources() -> None:
    assert _host_source({"extraction_strategy": "oracle_cloud"}) == "oracle_cloud"
    assert _host_source({"extraction_strategy": "sitemap_jsonld"}) == "career_site"
    assert _host_source({"extraction_strategy": "api_recipe", "api_recipe": {}}) == "career_site"


def test_oracle_careers_url_builder() -> None:
    assert _oracle_careers_url("CX_1", "acme.oraclecloud.com") == (
        "https://acme.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1")
    assert _oracle_careers_url("CX_1", "acme.oraclecloud.com/CX_2") == (
        "https://acme.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2")
    assert _oracle_careers_url("CX_1", None) is None


def test_oracle_never_gets_a_discovered_companies_board_token() -> None:
    # The nightly ATS harvest has no Oracle reader; a board_token row would
    # sit enabled and unharvested. Oracle integrates to career hosts instead.
    assert _board_token("oracle_cloud", "CX_1", "acme.oraclecloud.com") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
