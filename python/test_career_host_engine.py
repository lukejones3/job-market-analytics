"""Dependency-light tests for employer-first career host ingestion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip

from backfill_crawl_tenants import infer_crawl_tenant
import career_host_engine as engine
from career_host_engine import (
    CrawlStats,
    _brand_attribution_company,
    _classify_host_run,
    _jsonld_objects,
    _location_evidence,
    _maturation_interval_days,
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


def _posting(organization: str, identifier: str = "REQ-1") -> dict:
    return {
        "@type": "JobPosting",
        "title": "Senior Data Engineer",
        "description": "Build reliable data products. " * 10,
        "hiringOrganization": {"@type": "Organization", "name": organization},
        "jobLocation": {
            "@type": "Place",
            "address": {"addressLocality": "Chicago", "addressRegion": "IL", "addressCountry": "US"},
        },
        "identifier": {"value": identifier},
        "url": "https://careers.acme.com/jobs/req-1",
        "datePosted": "2026-08-09",
        "validThrough": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "directApply": True,
    }


def test_brand_posting_on_employer_domain_is_attributed_to_stated_brand() -> None:
    host = {
        "host_id": "CH123", "company_name": "Acme Parent",
        "jobs_host": "careers.acme.com", "status": "shadow",
    }
    stats = CrawlStats()
    job = posting_to_job(host, _posting("Globex"), "https://careers.acme.com/jobs/req-1", stats)

    assert job is not None
    assert job.company == "Globex"
    assert job.metadata["identity_attribution"] == "posting_brand"
    assert job.metadata["host_company_name"] == "Acme Parent"
    assert stats.host_attributed_jobs == 0
    assert stats.brand_attributed_jobs == 1
    assert stats.identity_mismatches == 0


def test_brand_attribution_requires_the_employer_domain() -> None:
    host = {"company_name": "Acme Parent", "jobs_host": "careers.acme.com"}
    assert _brand_attribution_company(host, "Globex", "https://careers.acme.com/jobs/1") == "Globex"
    assert _brand_attribution_company(host, "Globex", "https://builtin.com/jobs/1") is None


def test_brand_minority_can_corroborate_clean_run_but_brand_takeover_cannot() -> None:
    jobs = [object(), object()]
    minority = CrawlStats(target_jobs=100, host_attributed_jobs=1, brand_attributed_jobs=1)
    assert _classify_host_run(jobs, minority, {}) == ("complete_nonzero", True)

    takeover = CrawlStats(target_jobs=100, host_attributed_jobs=1, brand_attributed_jobs=30)
    assert _classify_host_run(jobs, takeover, {}) == ("complete_nonzero", False)


def test_zero_mismatch_hosts_mature_in_two_days_only_after_prior_clean_crawl() -> None:
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    assert _maturation_interval_days(
        identity_mismatches=0, job_count=3,
        first_clean_crawl_at=now - timedelta(days=3), now=now,
    ) == 2
    assert _maturation_interval_days(
        identity_mismatches=1, job_count=3,
        first_clean_crawl_at=now - timedelta(days=3), now=now,
    ) == 7
    assert _maturation_interval_days(
        identity_mismatches=0, job_count=0,
        first_clean_crawl_at=now - timedelta(days=3), now=now,
    ) == 7
    assert _maturation_interval_days(
        identity_mismatches=0, job_count=3,
        first_clean_crawl_at=now - timedelta(days=1), now=now,
    ) == 7
    assert _maturation_interval_days(
        identity_mismatches=0, job_count=3, first_clean_crawl_at=None, now=now,
    ) == 7


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        self.calls.append((sql, params))
        if "FROM career_host_candidates" in sql:
            self._rows = [{
                "candidate_id": 42, "company_key": "acme", "evidence": {},
            }]
        elif "FROM career_hosts" in sql:
            self._rows = [{
                "host_id": "CH123", "company_key": "acme",
                "identity_status": "needs_review", "first_clean_crawl_at": None,
            }]

    def fetchall(self) -> list[dict]:
        return self._rows


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def cursor(self, cursor_factory=None):
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def test_review_requeue_records_audit_and_resets_host_maturation(monkeypatch) -> None:
    fake = _FakeConnection()
    monkeypatch.setattr(engine, "connection", lambda: fake)

    result = engine.requeue_review_items(
        apply=True, limit=10, actor="evan", reason="reviewed employer evidence",
        entity="all",
    )

    assert result["candidates_requeued"] == 1
    assert result["hosts_requeued"] == 1
    assert fake.committed is True
    sql = "\n".join(call[0] for call in fake.cursor_instance.calls)
    assert sql.count("career_host_review_actions") == 2
    assert "SET status='pending'" in sql
    assert "status='shadow'" in sql
    assert "first_clean_crawl_at=NULL" in sql
    assert "source_quality_status='quarantine'" in sql
