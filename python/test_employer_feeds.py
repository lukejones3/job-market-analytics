"""Employer-feed tolerance, registry identity, and crawl accounting."""
from pathlib import Path

import coverage_ingest
import crawl_observability
import employer_feeds
from coverage_ingest import employer_feed_with_report
from employer_feeds import EmployerFeedPartner, process_partner, source_status
from ingest_jobs import RawJob


class _Response:
    def __init__(self, *, payload=None, text="", content_type="application/json"):
        self._payload = payload
        self.text = text
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _partner(key="state-university"):
    return EmployerFeedPartner(
        partner_key=key,
        name="State University jobs",
        feed_url="https://example.edu/jobs.json",
        employer_name="State University",
        feed_format="json",
    )


def test_employer_feed_skips_missing_id_and_continues(monkeypatch):
    monkeypatch.setattr(coverage_ingest, "is_target_role", lambda title: "Data" in title)
    payload = {
        "jobs": [
            {
                "id": "REQ-1",
                "title": "Data Analyst",
                "company": "Wrong Name From Row",
                "location": "Austin, TX",
                "description": "Analyze things for a long and useful description.",
                "url": "https://example.edu/jobs/REQ-1",
                "posted_date": "2026-10-01",
            },
            {"title": "Data Engineer", "location": "Remote"},
            "not-a-row",
            {"id": "REQ-2", "title": "Janitor", "location": "Austin, TX"},
        ]
    }
    monkeypatch.setattr(
        coverage_ingest.requests,
        "get",
        lambda *args, **kwargs: _Response(payload=payload),
    )

    result = employer_feed_with_report(
        "https://example.edu/jobs.json",
        employer_name="State University",
        tenant="state-university",
    )

    assert result.rows_seen == 4
    assert result.rows_ok == 1
    assert result.rows_bad == 2
    assert result.rows_non_target == 1
    assert [error.row_number for error in result.errors] == [2, 3]
    job = result.jobs[0]
    assert job.company == "State University"  # registered identity wins
    assert job.source_id == "State University|state-university|REQ-1"
    assert job.metadata["tenant"] == "state-university"
    assert job.metadata["hiring_organization"] == "State University"


def test_employer_feed_csv_skips_bad_posted_date(monkeypatch):
    monkeypatch.setattr(coverage_ingest, "is_target_role", lambda title: True)
    csv_text = (
        "id,title,location,description,url,posted_date\n"
        "1,Data Analyst,\"Denver, CO\",Useful description,https://example.org/1,2026-10-01\n"
        "2,Data Engineer,\"Denver, CO\",Useful description,https://example.org/2,not-a-date\n"
    )
    monkeypatch.setattr(
        coverage_ingest.requests,
        "get",
        lambda *args, **kwargs: _Response(text=csv_text, content_type="text/csv"),
    )

    result = employer_feed_with_report("https://example.org/feed", feed_format="csv")

    assert result.rows_seen == 2
    assert result.rows_ok == 1
    assert result.rows_bad == 1
    assert "posted_date" in result.errors[0].reason


def test_process_partner_records_partial_failure_and_dedupes(monkeypatch):
    crawl_observability.reset()
    job = RawJob(
        source="employer_feed",
        source_id="State University|REQ-1",
        title="Data Analyst",
        company="State University",
        location="Austin, TX",
        description="Useful description",
        job_url="https://example.edu/jobs/REQ-1",
        metadata={"tenant": "state-university"},
    )

    def fake_fetcher(*args, **kwargs):
        return coverage_ingest.EmployerFeedParseResult(
            jobs=[job, job],
            rows_seen=3,
            rows_ok=2,
            rows_bad=1,
            rows_non_target=0,
            errors=[coverage_ingest.EmployerFeedRowError(2, "missing id/requisition_id")],
        )

    writer_calls = []

    def fake_writer(jobs):
        writer_calls.append(jobs)
        return 1, 0, 0

    result = process_partner(
        _partner(),
        apply=True,
        fetcher=fake_fetcher,
        job_writer=fake_writer,
    )

    assert result.status == "partial_failure"
    assert result.duplicate_rows == 1
    assert result.jobs_considered == 1
    assert result.jobs_written == 1
    assert len(writer_calls[0]) == 1
    outcome = crawl_observability.snapshot("employer_feed")[0]
    assert outcome.crawl_tenant == "state-university"
    assert outcome.status == "partial_failure"
    assert outcome.jobs_fetched == 3  # attempted rows, not filtered survivors
    assert outcome.detail["rows_bad"] == 1


def test_process_partner_fatal_fetch_is_failed_not_complete_zero():
    crawl_observability.reset()

    def broken_fetcher(*args, **kwargs):
        raise RuntimeError("partner returned HTTP 503")

    result = process_partner(
        _partner("broken-college"),
        apply=False,
        fetcher=broken_fetcher,
        job_writer=lambda jobs: (0, 0, 0),
    )

    assert result.status == "failed"
    assert result.rows_seen == 0
    outcome = crawl_observability.snapshot("employer_feed")[0]
    assert outcome.status == "failed"
    assert source_status([result]) == "source_failure"


def test_process_partner_dry_run_records_attempt_without_writing():
    crawl_observability.reset()
    job = RawJob(
        source="employer_feed",
        source_id="State University|REQ-9",
        title="Data Scientist",
        company="State University",
    )

    def fake_fetcher(*args, **kwargs):
        return coverage_ingest.EmployerFeedParseResult(
            jobs=[job], rows_seen=1, rows_ok=1
        )

    def forbidden_writer(jobs):  # pragma: no cover - assertion guard
        raise AssertionError("dry run must not write")

    result = process_partner(
        _partner(),
        apply=False,
        fetcher=fake_fetcher,
        job_writer=forbidden_writer,
    )

    assert result.status == "complete_nonzero"
    assert result.jobs_written == 0


def test_validate_partner_rejects_unsafe_registry_values():
    partner = _partner()
    employer_feeds.validate_partner(partner)

    bad = EmployerFeedPartner(
        partner_key="Bad Key",
        name="Bad",
        feed_url="ftp://example.edu/feed",
        employer_name="Bad Employer",
    )
    try:
        employer_feeds.validate_partner(bad)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "partner_key" in str(exc)


def test_employer_feed_registry_sql_is_idempotent_and_complete():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "employer_feeds.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS employer_feed_partners" in sql
    assert "CREATE TABLE IF NOT EXISTS employer_feed_runs" in sql
    for column in (
        "partner_key",
        "feed_url",
        "feed_format",
        "employer_name",
        "employer_domain",
        "employer_company_id",
        "enabled",
        "rows_seen",
        "rows_ok",
        "rows_bad",
        "jobs_written",
    ):
        assert column in sql
    assert "CREATE INDEX IF NOT EXISTS" in sql
