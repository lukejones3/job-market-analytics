"""Dependency-light tests for employer-first career host ingestion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip

from backfill_crawl_tenants import infer_crawl_tenant
from career_host_engine import (
    CrawlStats,
    _classify_host_run,
    _jsonld_objects,
    _location_evidence,
    _parse_sitemap,
    company_key,
    fingerprint_url,
    is_blocked_result,
    organization_matches,
    posting_to_job,
)
from crawl_observability import record_failure, record_success, reset, snapshot
from validate_ats_candidates import _is_us_job


def test_company_identity_key_removes_legal_suffixes() -> None:
    assert company_key("Acme Technologies, Inc.") == "acme"
    assert company_key("Booz Allen Hamilton") == "booz-allen-hamilton"


def test_platform_fingerprints_keep_required_locator_parts() -> None:
    workday = fingerprint_url("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/123")
    assert workday and workday.platform == "workday"
    assert workday.tenant_token == "acme"
    assert workday.server == "wd5/Careers"

    oracle = fingerprint_url(
        "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs"
    )
    assert oracle and oracle.platform == "oracle_cloud"
    assert oracle.tenant_token == "cx_1"


def test_blocked_resolver_results_include_aggregators_and_documents() -> None:
    assert is_blocked_result("https://jobs.dejobs.org/jobs/123")
    assert is_blocked_result("https://cdn.example.com/posters/e-verify.PDF?download=1")
    assert not is_blocked_result("https://careers.example.com/jobs/123")


def test_nested_jobposting_and_gzip_sitemap_are_supported() -> None:
    graph = {"@graph": [{"@type": "Organization"}, {"@type": "JobPosting", "title": "Data Engineer"}]}
    assert [row["title"] for row in _jsonld_objects(graph)] == ["Data Engineer"]
    xml = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://acme.com/jobs/1</loc></url></urlset>'
    indexes, pages = _parse_sitemap(gzip.compress(xml), "https://acme.com/jobs.xml.gz")
    assert indexes == []
    assert pages == ["https://acme.com/jobs/1"]
    # Some HTTP clients decode Content-Encoding before returning *.gz bytes.
    assert _parse_sitemap(xml, "https://acme.com/jobs.xml.gz")[1] == pages


def test_partial_crawl_cannot_activate_or_expire_a_host() -> None:
    jobs = [object()]
    status, clean = _classify_host_run(jobs, CrawlStats(errors=1), {})
    assert status == "partial_failure"
    assert clean is False
    status, clean = _classify_host_run(jobs, CrawlStats(), {"sitemap_errors": 1})
    assert status == "partial_failure"
    assert clean is False


def test_failed_speculative_sitemap_root_does_not_poison_complete_crawl() -> None:
    detail = {
        "sitemap_root_successes": 1,
        "sitemap_root_errors": 2,
        "sitemap_child_errors": 0,
        "sitemap_errors": 2,
    }
    status, clean = _classify_host_run([object()], CrawlStats(), detail)
    assert status == "complete_nonzero"
    assert clean is True
    detail["sitemap_child_errors"] = 1
    status, clean = _classify_host_run([object()], CrawlStats(), detail)
    assert status == "partial_failure"
    assert clean is False


def test_remote_requires_explicit_us_applicant_eligibility() -> None:
    global_remote = {"jobLocationType": "TELECOMMUTE"}
    assert _location_evidence(global_remote) is None
    us_remote = {
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "United States"},
    }
    assert _location_evidence(us_remote) == (
        "Remote, United States",
        {"country": "US", "kind": "applicantLocationRequirements"},
    )
    australia_remote = {
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "Australia"},
    }
    assert _location_evidence(australia_remote) is None


def test_india_signal_does_not_reject_indiana() -> None:
    assert _is_us_job("Indianapolis, IN")
    assert not _is_us_job("Bengaluru, India")
    assert not _is_us_job("")


def test_hiring_organization_gate_allows_alias_shape_not_unrelated_board() -> None:
    assert organization_matches("Meta Platforms, Inc.", "Meta")
    assert organization_matches("The Home Depot", "Home Depot USA")
    assert not organization_matches("Acme", "Built In")


def test_posting_becomes_quarantined_raw_job_until_host_matures() -> None:
    host = {"host_id": "CH123", "company_name": "Acme", "status": "shadow"}
    posting = {
        "@type": "JobPosting",
        "title": "Senior Data Engineer",
        "description": "Build reliable data products. " * 10,
        "hiringOrganization": {"@type": "Organization", "name": "Acme, Inc."},
        "jobLocation": {
            "@type": "Place",
            "address": {"addressLocality": "Chicago", "addressRegion": "IL", "addressCountry": "US"},
        },
        "identifier": {"value": "REQ-1"},
        "url": "https://acme.com/jobs/req-1",
        "datePosted": "2026-08-09",
        "validThrough": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "directApply": True,
    }
    stats = CrawlStats()
    job = posting_to_job(host, posting, posting["url"], stats)
    assert job is not None
    assert job.metadata["source_quality_status"] == "quarantine"
    assert job.metadata["location_evidence"]["country"] == "US"
    assert stats.accepted_jobs == 1


def test_tenant_outcomes_preserve_partial_failure_instead_of_false_zero() -> None:
    reset()
    record_success("workday", "acme", 20, board="Careers")
    record_failure("workday", "acme", "page 2 throttled", partial=True)
    outcome = snapshot("workday")[0]
    assert outcome.status == "partial_failure"
    assert outcome.jobs_fetched == 20
    reset()
    record_success("greenhouse", "empty-board", 0)
    assert snapshot("greenhouse")[0].status == "complete_zero"


def test_historical_tenant_repair_is_source_specific() -> None:
    assert infer_crawl_tenant("workday", None, "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/1") == "acme"
    assert infer_crawl_tenant("greenhouse", None, "https://job-boards.greenhouse.io/stripe/jobs/1") == "stripe"
    assert infer_crawl_tenant("jobvite", "contoso|REQ-1", None) == "contoso"
