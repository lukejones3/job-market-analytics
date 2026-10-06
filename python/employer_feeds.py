#!/usr/bin/env python3
"""Nightly runner and operator CLI for registered employer feeds.

The registry is deliberately employer-direct. A feed is publication rank 0,
so this runner preserves that trust boundary: the registered employer name is
authoritative, partner_key becomes crawl_tenant, bad rows are skipped with
counts, and every partner gets tenant-level crawl accounting.
"""
from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from psycopg2.extras import DictCursor, Json

from coverage_ingest import employer_feed_with_report
from crawl_observability import record_failure, record_success, reset, snapshot
from ingest_jobs import (
    RawJob,
    _ingest_job_with_savepoint,
    ensure_schema_columns,
    get_conn,
)
from location_normalizer import normalize_location

SOURCE = "employer_feed"
ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SQL = ROOT / "sql" / "employer_feeds.sql"
_PARTNER_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmployerFeedPartner:
    partner_key: str
    name: str
    feed_url: str
    employer_name: str
    feed_format: str = "auto"
    employer_domain: str | None = None
    employer_company_id: str | None = None
    auth_env_var: str | None = None
    enabled: bool = True


@dataclass
class EmployerFeedResult:
    partner_key: str
    employer_name: str
    status: str = "failed"
    rows_seen: int = 0
    rows_ok: int = 0
    rows_bad: int = 0
    rows_non_target: int = 0
    duplicate_rows: int = 0
    jobs_considered: int = 0
    jobs_written: int = 0
    jobs_skipped: int = 0
    jobs_write_errors: int = 0
    error: str | None = None
    error_samples: list[str] = field(default_factory=list)

    @property
    def errors_total(self) -> int:
        return self.rows_bad + self.jobs_write_errors + (1 if self.status == "failed" and self.error else 0)

    def detail(self) -> dict:
        return {
            "rows_seen": self.rows_seen,
            "rows_ok": self.rows_ok,
            "rows_bad": self.rows_bad,
            "rows_non_target": self.rows_non_target,
            "duplicate_rows": self.duplicate_rows,
            "jobs_considered": self.jobs_considered,
            "jobs_written": self.jobs_written,
            "jobs_skipped": self.jobs_skipped,
            "jobs_write_errors": self.jobs_write_errors,
            "error": self.error,
            "error_samples": self.error_samples[:20],
        }


def validate_partner(partner: EmployerFeedPartner) -> None:
    if not _PARTNER_KEY_RE.fullmatch(partner.partner_key):
        raise ValueError("partner_key must be lowercase letters, digits, '-' or '_'")
    if not partner.name.strip() or not partner.employer_name.strip():
        raise ValueError("partner name and employer_name are required")
    parsed = urlparse(partner.feed_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("feed_url must be an absolute http(s) URL")
    if partner.feed_format not in {"auto", "json", "csv"}:
        raise ValueError("feed_format must be auto, json, or csv")
    if partner.auth_env_var and not _ENV_VAR_RE.fullmatch(partner.auth_env_var):
        raise ValueError("auth_env_var must be an environment variable name")


def _ensure_registry_schema(cur) -> None:
    cur.execute(REGISTRY_SQL.read_text(encoding="utf-8"))


def _ensure_ingestion_observability_schema(cur) -> None:
    # Resolve from the repository root instead of depending on the caller's
    # cwd; the DAG also applies this file, and the DDL is idempotent.
    cur.execute((ROOT / "sql" / "ingestion_observability.sql").read_text(encoding="utf-8"))


def _partner_from_row(row) -> EmployerFeedPartner:
    return EmployerFeedPartner(
        partner_key=row["partner_key"],
        name=row["name"],
        feed_url=row["feed_url"],
        employer_name=row["employer_name"],
        feed_format=row["feed_format"] or "auto",
        employer_domain=row["employer_domain"],
        employer_company_id=row["employer_company_id"],
        auth_env_var=row["auth_env_var"],
        enabled=bool(row["enabled"]),
    )


def load_partners(
    partner_keys: list[str] | None = None,
    *,
    include_disabled: bool = False,
) -> list[EmployerFeedPartner]:
    """Load registry partners in stable crawl-tenant order."""
    clauses = []
    params: list = []
    if not include_disabled:
        clauses.append("enabled = true")
    if partner_keys:
        clauses.append("partner_key = ANY(%s)")
        params.append(partner_keys)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            _ensure_registry_schema(cur)
            cur.execute(
                f"""
                SELECT partner_key, name, feed_url, feed_format, employer_name,
                       employer_domain, employer_company_id, auth_env_var, enabled
                FROM employer_feed_partners
                {where}
                ORDER BY partner_key
                """,
                params,
            )
            partners = [_partner_from_row(row) for row in cur.fetchall()]
        conn.commit()
        return partners
    finally:
        conn.close()


def load_enabled_partners(partner_keys: list[str] | None = None) -> list[EmployerFeedPartner]:
    return load_partners(partner_keys, include_disabled=False)


def register_partner(partner: EmployerFeedPartner, *, apply: bool) -> None:
    validate_partner(partner)
    if not apply:
        print(
            "Would register employer feed "
            f"{partner.partner_key}: {partner.employer_name} -> {partner.feed_url}"
        )
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_registry_schema(cur)
            cur.execute(
                """
                INSERT INTO employer_feed_partners
                    (partner_key, name, feed_url, feed_format, employer_name,
                     employer_domain, employer_company_id, auth_env_var, enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (partner_key) DO UPDATE SET
                    name = EXCLUDED.name,
                    feed_url = EXCLUDED.feed_url,
                    feed_format = EXCLUDED.feed_format,
                    employer_name = EXCLUDED.employer_name,
                    employer_domain = EXCLUDED.employer_domain,
                    employer_company_id = EXCLUDED.employer_company_id,
                    auth_env_var = EXCLUDED.auth_env_var,
                    enabled = EXCLUDED.enabled,
                    updated_at = now()
                """,
                (
                    partner.partner_key,
                    partner.name,
                    partner.feed_url,
                    partner.feed_format,
                    partner.employer_name,
                    partner.employer_domain,
                    partner.employer_company_id,
                    partner.auth_env_var,
                    partner.enabled,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    print(f"Registered employer feed {partner.partner_key} ({partner.employer_name})")


def _write_jobs(jobs: list[RawJob]) -> tuple[int, int, int]:
    """Write one partner's jobs; return (written, skipped, write_errors)."""
    written = skipped = errors = 0
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            ensure_schema_columns(cur)
            for job in jobs:
                if normalize_location(job.location, job.workplace_type).should_drop:
                    skipped += 1
                    continue
                try:
                    if _ingest_job_with_savepoint(cur, job):
                        written += 1
                    else:
                        skipped += 1
                except Exception as exc:  # isolate one bad DB row from the feed
                    errors += 1
                    log.error(
                        "Employer-feed write failed [%s/%s]: %s",
                        job.company,
                        job.source_id,
                        exc,
                    )
        conn.commit()
    finally:
        conn.close()
    return written, skipped, errors


def _record_outcome(result: EmployerFeedResult) -> None:
    detail = result.detail()
    detail.pop("error", None)  # record_failure supplies its own error field
    if result.status == "failed":
        record_failure(
            SOURCE,
            result.partner_key,
            result.error or "employer feed failed",
            **detail,
        )
        return

    # Completion is based on the attempted feed (rows_seen), not on how many
    # rows survived product filters. This mirrors crawl_observability's rule
    # for ATS boards: healthy-but-small feeds should not look broken.
    record_success(SOURCE, result.partner_key, result.rows_seen, **detail)
    if result.status == "partial_failure":
        record_failure(
            SOURCE,
            result.partner_key,
            result.error or "employer feed had malformed rows or write errors",
            partial=True,
            **detail,
        )


def process_partner(
    partner: EmployerFeedPartner,
    *,
    apply: bool,
    fetcher=employer_feed_with_report,
    job_writer=_write_jobs,
) -> EmployerFeedResult:
    """Fetch and optionally write one feed without letting it kill the run."""
    validate_partner(partner)
    result = EmployerFeedResult(
        partner_key=partner.partner_key,
        employer_name=partner.employer_name,
    )
    try:
        report = fetcher(
            partner.feed_url,
            employer_name=partner.employer_name,
            tenant=partner.partner_key,
            feed_format=partner.feed_format,
            # Registry credentials are opt-in per partner via auth_env_var.
            # Do not spray the legacy manual-feed token at every registry URL.
            auth_env_var=partner.auth_env_var,
        )
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)[:500]
        result.error_samples = [result.error]
        _record_outcome(result)
        log.error("Employer feed %s failed: %s", partner.partner_key, exc)
        return result

    result.rows_seen = report.rows_seen
    result.rows_ok = report.rows_ok
    result.rows_bad = report.rows_bad
    result.rows_non_target = report.rows_non_target
    result.error_samples = [
        f"row {error.row_number}: {error.reason}" for error in report.errors[:20]
    ]

    for job in report.jobs:
        if partner.employer_company_id:
            job.metadata["employer_company_id"] = partner.employer_company_id
        if partner.employer_domain:
            job.metadata["employer_domain"] = partner.employer_domain

    unique = {(job.source, job.source_id): job for job in report.jobs}
    result.duplicate_rows = len(report.jobs) - len(unique)
    result.jobs_considered = len(unique)

    if apply:
        try:
            written, skipped, errors = job_writer(list(unique.values()))
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)[:500]
            result.error_samples.append(result.error)
            _record_outcome(result)
            log.error("Employer feed %s write failed: %s", partner.partner_key, exc)
            return result
        result.jobs_written = written
        result.jobs_skipped = skipped
        result.jobs_write_errors = errors

    if result.rows_bad or result.jobs_write_errors:
        result.status = "partial_failure"
        result.error = result.error_samples[0] if result.error_samples else "row/write errors"
    elif result.rows_seen:
        result.status = "complete_nonzero"
    else:
        result.status = "complete_zero"

    _record_outcome(result)
    log.info(
        "Employer feed %s: rows_seen=%s rows_ok=%s rows_bad=%s "
        "rows_non_target=%s duplicates=%s considered=%s written=%s skipped=%s",
        partner.partner_key,
        result.rows_seen,
        result.rows_ok,
        result.rows_bad,
        result.rows_non_target,
        result.duplicate_rows,
        result.jobs_considered,
        result.jobs_written,
        result.jobs_skipped,
    )
    return result


def source_status(results: list[EmployerFeedResult]) -> str:
    if not results:
        return "complete_zero"
    if all(result.status == "failed" for result in results):
        return "source_failure"
    if any(result.status in {"failed", "partial_failure"} for result in results):
        return "partial_failure"
    return "complete_nonzero" if sum(result.rows_seen for result in results) else "complete_zero"


def _start_crawl_run(run_id: str, orchestration_run_id: str | None) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_registry_schema(cur)
            _ensure_ingestion_observability_schema(cur)
            cur.execute(
                """
                INSERT INTO ingestion_crawl_runs
                    (run_id, source, orchestration_run_id, status)
                VALUES (%s, %s, %s, 'running')
                ON CONFLICT (run_id) DO NOTHING
                """,
                (run_id, SOURCE, orchestration_run_id),
            )
        conn.commit()
    finally:
        conn.close()


def _finish_crawl_run(
    run_id: str,
    orchestration_run_id: str | None,
    results: list[EmployerFeedResult],
) -> None:
    outcomes = {outcome.crawl_tenant: outcome for outcome in snapshot(SOURCE)}
    status = source_status(results)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_registry_schema(cur)
            cur.execute(
                """
                UPDATE ingestion_crawl_runs
                SET finished_at = now(), status = %s, jobs_fetched = %s,
                    jobs_written = %s, errors = %s,
                    detail = detail || %s
                WHERE run_id = %s
                """,
                (
                    status,
                    sum(result.rows_seen for result in results),
                    sum(result.jobs_written for result in results),
                    sum(result.errors_total for result in results),
                    Json({
                        "partners": len(results),
                        "rows_bad": sum(result.rows_bad for result in results),
                        "rows_non_target": sum(result.rows_non_target for result in results),
                    }),
                    run_id,
                ),
            )
            for result in results:
                outcome = outcomes.get(result.partner_key)
                tenant_status = outcome.status if outcome else result.status
                if tenant_status == "source_failure":
                    tenant_status = "failed"
                cur.execute(
                    """
                    INSERT INTO employer_feed_runs
                        (run_id, partner_key, orchestration_run_id, finished_at, status,
                         rows_seen, rows_ok, rows_bad, rows_non_target, duplicate_rows,
                         jobs_considered, jobs_written, jobs_skipped, jobs_write_errors, detail)
                    VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, partner_key) DO UPDATE SET
                        finished_at = EXCLUDED.finished_at,
                        status = EXCLUDED.status,
                        rows_seen = EXCLUDED.rows_seen,
                        rows_ok = EXCLUDED.rows_ok,
                        rows_bad = EXCLUDED.rows_bad,
                        rows_non_target = EXCLUDED.rows_non_target,
                        duplicate_rows = EXCLUDED.duplicate_rows,
                        jobs_considered = EXCLUDED.jobs_considered,
                        jobs_written = EXCLUDED.jobs_written,
                        jobs_skipped = EXCLUDED.jobs_skipped,
                        jobs_write_errors = EXCLUDED.jobs_write_errors,
                        detail = EXCLUDED.detail
                    """,
                    (
                        run_id,
                        result.partner_key,
                        orchestration_run_id,
                        result.status,
                        result.rows_seen,
                        result.rows_ok,
                        result.rows_bad,
                        result.rows_non_target,
                        result.duplicate_rows,
                        result.jobs_considered,
                        result.jobs_written,
                        result.jobs_skipped,
                        result.jobs_write_errors,
                        Json(result.detail()),
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO ingestion_tenant_runs
                        (run_id, source, crawl_tenant, status, jobs_fetched, errors, detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, source, crawl_tenant) DO UPDATE SET
                        status = EXCLUDED.status,
                        jobs_fetched = EXCLUDED.jobs_fetched,
                        errors = EXCLUDED.errors,
                        detail = EXCLUDED.detail
                    """,
                    (
                        run_id,
                        SOURCE,
                        result.partner_key,
                        tenant_status,
                        outcome.jobs_fetched if outcome else result.rows_seen,
                        outcome.errors if outcome else result.errors_total,
                        Json(result.detail()),
                    ),
                )
                cur.execute(
                    """
                    UPDATE employer_feed_partners
                    SET last_run_at = now(), last_status = %s,
                        last_rows_seen = %s, last_rows_ok = %s,
                        last_rows_bad = %s, last_jobs_written = %s,
                        updated_at = now()
                    WHERE partner_key = %s
                    """,
                    (
                        result.status,
                        result.rows_seen,
                        result.rows_ok,
                        result.rows_bad,
                        result.jobs_written,
                        result.partner_key,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _print_report(run_id: str, results: list[EmployerFeedResult], *, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN"
    print(f"Employer feeds {mode} run_id={run_id} partners={len(results)} status={source_status(results)}")
    for result in results:
        print(
            f"  {result.partner_key}: status={result.status} rows_seen={result.rows_seen} "
            f"rows_ok={result.rows_ok} rows_bad={result.rows_bad} "
            f"rows_non_target={result.rows_non_target} duplicates={result.duplicate_rows} "
            f"considered={result.jobs_considered} written={result.jobs_written} "
            f"skipped={result.jobs_skipped} write_errors={result.jobs_write_errors}"
        )


def run_registered_feeds(
    *,
    apply: bool = False,
    partner_keys: list[str] | None = None,
    include_disabled: bool = False,
    orchestration_run_id: str | None = None,
) -> list[EmployerFeedResult]:
    run_id = (
        f"employer_feed_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
    )
    reset()
    partners = load_partners(partner_keys, include_disabled=include_disabled)
    if apply:
        _start_crawl_run(run_id, orchestration_run_id)

    results: list[EmployerFeedResult] = []
    for partner in partners:
        try:
            results.append(process_partner(partner, apply=apply))
        except Exception as exc:  # registry loop must survive one partner
            result = EmployerFeedResult(
                partner_key=partner.partner_key,
                employer_name=partner.employer_name,
                status="failed",
                error=str(exc)[:500],
                error_samples=[str(exc)[:500]],
            )
            _record_outcome(result)
            results.append(result)
            log.error("Employer feed %s failed unexpectedly: %s", partner.partner_key, exc)

    if apply:
        _finish_crawl_run(run_id, orchestration_run_id, results)
    _print_report(run_id, results, apply=apply)
    return results


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Registered employer-feed runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List registered feeds")
    list_parser.add_argument("--all", action="store_true", help="Include disabled partners")

    register = subparsers.add_parser("register", help="Create or update one partner")
    register.add_argument("--partner-key", required=True)
    register.add_argument("--name", required=True, help="Operator-facing partner name")
    register.add_argument("--url", required=True)
    register.add_argument("--format", choices=("auto", "json", "csv"), default="auto")
    register.add_argument("--employer-name", required=True)
    register.add_argument("--employer-domain")
    register.add_argument("--employer-company-id")
    register.add_argument("--auth-env-var")
    register.add_argument("--disabled", action="store_true")
    register.add_argument("--apply", action="store_true", help="Write the registry row")

    run = subparsers.add_parser("run", help="Run registered feeds")
    run.add_argument("--partner", action="append", dest="partners")
    run.add_argument("--include-disabled", action="store_true")
    run.add_argument("--orchestration-run-id")
    run.add_argument("--apply", action="store_true", help="Write jobs and crawl accounting")

    args = parser.parse_args()
    if args.command == "list":
        partners = load_partners(include_disabled=args.all)
        for partner in partners:
            state = "enabled" if partner.enabled else "disabled"
            print(
                f"{partner.partner_key}\t{state}\t{partner.employer_name}\t"
                f"{partner.feed_format}\t{partner.feed_url}"
            )
        return 0

    if args.command == "register":
        partner = EmployerFeedPartner(
            partner_key=args.partner_key,
            name=args.name,
            feed_url=args.url,
            employer_name=args.employer_name,
            feed_format=args.format,
            employer_domain=args.employer_domain,
            employer_company_id=args.employer_company_id,
            auth_env_var=args.auth_env_var,
            enabled=not args.disabled,
        )
        register_partner(partner, apply=args.apply)
        return 0

    results = run_registered_feeds(
        apply=args.apply,
        partner_keys=args.partners,
        include_disabled=args.include_disabled,
        orchestration_run_id=args.orchestration_run_id,
    )
    # A total registry outage should fail the DAG task. Partial partner damage
    # is reported in accounting and should not block the wider nightly gate.
    return 1 if results and all(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
