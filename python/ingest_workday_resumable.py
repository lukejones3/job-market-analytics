#!/usr/bin/env python3
"""Run Workday or SmartRecruiters in durable, retryable tenant batches.

The core ingestor intentionally fetches a complete source before writing it.
That is safe for small ATS inventories, but the Workday universe is now large
enough to exceed one Airflow task window.  This wrapper scopes each child run
to a small set of tenants.  Successful child runs commit normally, and an
Airflow retry skips tenants already completed for the same orchestration run.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from psycopg2.extras import Json
from pathlib import Path
from typing import Iterable, Sequence

import ingest_jobs
from workday_boards import order_tenants_by_server


def unique_tenants(rows: Iterable[Sequence[str]]) -> list[str]:
    """Keep Workday tenant order while collapsing boards for the same tenant."""
    seen: set[str] = set()
    tenants: list[str] = []
    for row in rows:
        tenant = str(row[1]).strip().lower()
        if tenant and tenant not in seen:
            seen.add(tenant)
            tenants.append(tenant)
    return tenants


def batches(values: Sequence[str], size: int) -> Iterable[list[str]]:
    if size < 1:
        raise ValueError("batch size must be positive")
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def completed_tenants(orchestration_run_id: str, source: str = "workday") -> set[str]:
    conn = ingest_jobs.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT lower(itr.crawl_tenant)
                   FROM ingestion_tenant_runs itr
                   JOIN ingestion_crawl_runs icr USING (run_id)
                   WHERE icr.orchestration_run_id=%s
                     AND itr.source=%s
                     AND icr.status IN ('complete_nonzero','complete_zero') AND icr.errors=0
                     AND itr.status IN ('complete_nonzero','complete_zero')""",
                (orchestration_run_id, source),
            )
            return {row[0] for row in cur.fetchall() if row[0]}
    finally:
        conn.close()


def run_batch(
    tenants: Sequence[str], orchestration_run_id: str, *, apply: bool, source: str = "workday"
) -> None:
    env = os.environ.copy()
    filter_key = "WORKDAY_TENANT_FILTER" if source == "workday" else "SMARTRECRUITERS_TENANT_FILTER"
    env[filter_key] = ",".join(tenants)
    env["WORKDAY_TENANT_TIMEOUT_SECONDS"] = os.getenv("WORKDAY_CHECKPOINT_TENANT_TIMEOUT_SECONDS", "600")
    # A smaller request fan-out is materially faster than repeatedly triggering
    # provider-wide five-minute cooldowns.
    env["WORKDAY_TENANT_CONCURRENCY"] = os.getenv(
        "WORKDAY_CHECKPOINT_TENANT_CONCURRENCY", "4"
    )
    env["WORKDAY_GLOBAL_CONCURRENCY"] = os.getenv(
        "WORKDAY_CHECKPOINT_GLOBAL_CONCURRENCY", "8"
    )
    env["WORKDAY_GLOBAL_MIN_INTERVAL"] = os.getenv(
        "WORKDAY_CHECKPOINT_GLOBAL_MIN_INTERVAL", "0.20"
    )
    command = [
        sys.executable,
        str(Path(__file__).with_name("ingest_jobs.py")),
        "--source",
        source,
        "--orchestration-run-id",
        orchestration_run_id,
        # A scoped batch may legitimately contain only tenants with no current
        # target openings. The child still records each verified tenant as
        # complete_zero; treating that as a provider outage strands the DAG.
        "--accept-empty",
    ]
    if apply:
        command.append("--apply")
    subprocess.run(command, env=env, check=True)


class TenantBatchFailures(RuntimeError):
    """The sweep attempted every batch, but some providers were unavailable."""


def run_checkpoints(pending, orchestration_run_id, *, apply, source, batch_size, delay):
    failed = []
    pending_batches = list(batches(pending, batch_size))
    for position, tenant_batch in enumerate(pending_batches, start=1):
        print(f"{source} checkpoint batch {position}/{len(pending_batches)}: {','.join(tenant_batch)}", flush=True)
        try:
            run_batch(tenant_batch, orchestration_run_id, apply=apply, source=source)
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 3:
                raise  # Program/database failures must stop, not masquerade as outages.
            failed.extend(tenant_batch)
            print(f"Deferred failed tenant batch; continuing remaining {source} inventory", flush=True)
        if position < len(pending_batches) and delay > 0:
            time.sleep(delay)
    if failed:
        raise TenantBatchFailures(f"{source}: sweep finished with {len(failed)} tenants in failed batches; clean checkpoints preserved")


def sweep_record(run_id, source, orchestration_run_id, inventory, *, finished=False, fatal=False, degraded=False):
    clean = completed_tenants(orchestration_run_id, source) if finished else set()
    healthy = len(set(inventory) & clean)
    terminal = set()
    if finished:
        with ingest_jobs.get_conn() as conn, conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT lower(itr.crawl_tenant)
                FROM ingestion_tenant_runs itr JOIN ingestion_crawl_runs icr USING(run_id)
                WHERE icr.orchestration_run_id=%s AND itr.source=%s
                  AND icr.finished_at IS NOT NULL AND icr.errors=0
                  AND itr.status IN ('complete_nonzero','complete_zero','partial_failure','failed')""",
                (orchestration_run_id, source))
            terminal = {row[0] for row in cur.fetchall()}
        fatal = fatal or bool(set(inventory) - terminal)
    status = ('source_failure' if fatal or (finished and inventory and not healthy) else
              'complete_nonzero' if healthy else 'complete_zero') if finished else 'running'
    detail = {'checkpoint_sweep': True, 'planned_tenants': inventory,
              'healthy_tenants': healthy, 'unaccounted_tenants': sorted(set(inventory)-terminal) if finished else [],
              'degraded': degraded or (finished and healthy < len(inventory))}
    with ingest_jobs.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO ingestion_crawl_runs(run_id,source,orchestration_run_id,status,errors,detail,finished_at)
            VALUES(%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END)
            ON CONFLICT(run_id) DO UPDATE SET status=EXCLUDED.status,errors=EXCLUDED.errors,
                detail=EXCLUDED.detail,finished_at=EXCLUDED.finished_at""",
            (run_id, source, orchestration_run_id, status, int(fatal), Json(detail), finished))
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("workday", "smartrecruiters"), default="workday")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--orchestration-run-id", required=True)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("WORKDAY_CHECKPOINT_BATCH_SIZE", "8")),
    )
    parser.add_argument(
        "--batch-delay-seconds",
        type=float,
        default=float(os.getenv("WORKDAY_CHECKPOINT_DELAY_SECONDS", "2")),
    )
    args = parser.parse_args()

    # Spread wd servers across the sequential batches: tenants sharing a
    # host share its rate limits, so a batch drawn from one server spends
    # its whole window fighting that host's throttles.
    if args.source == "workday":
        inventory = order_tenants_by_server(ingest_jobs._load_workday_list())
    else:
        with ingest_jobs.get_conn() as conn, conn.cursor() as cur:
            cur.execute("""SELECT company_name,board_token FROM discovered_companies
                WHERE ats_source='smartrecruiters' AND enabled=true
                  AND board_token IS NOT NULL AND btrim(board_token)<>''
                ORDER BY active_roles DESC NULLS LAST,board_token""")
            inventory = unique_tenants(cur.fetchall())
    completed = completed_tenants(args.orchestration_run_id, args.source) if args.apply else set()
    pending = [tenant for tenant in inventory if tenant not in completed]
    print(
        f"{args.source} checkpoint plan: inventory={len(inventory)} "
        f"completed={len(completed)} pending={len(pending)} batch_size={args.batch_size}",
        flush=True,
    )

    sweep_id = f'checkpoint_{args.source}_{uuid.uuid4().hex}'
    if args.apply:
        sweep_record(sweep_id, args.source, args.orchestration_run_id, inventory)
    try:
        run_checkpoints(pending, args.orchestration_run_id, apply=args.apply, source=args.source,
                        batch_size=args.batch_size, delay=args.batch_delay_seconds)
    except TenantBatchFailures:
        if args.apply:
            sweep_record(sweep_id, args.source, args.orchestration_run_id, inventory, finished=True, degraded=True)
        raise
    except BaseException:
        if args.apply:
            sweep_record(sweep_id, args.source, args.orchestration_run_id, inventory, finished=True, fatal=True)
        raise
    else:
        if args.apply:
            status = sweep_record(sweep_id, args.source, args.orchestration_run_id, inventory, finished=True)
            if status == 'source_failure':
                raise RuntimeError(f'{args.source}: sweep has unaccounted or entirely failed tenants')
    print(f"{args.source} checkpoint ingestion complete", flush=True)


if __name__ == "__main__":
    main()
