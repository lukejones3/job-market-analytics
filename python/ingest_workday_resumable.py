#!/usr/bin/env python3
"""Run the Workday inventory in durable, retryable tenant batches.

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
from pathlib import Path
from typing import Iterable, Sequence

import ingest_jobs


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


def completed_tenants(orchestration_run_id: str) -> set[str]:
    conn = ingest_jobs.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT lower(itr.crawl_tenant)
                   FROM ingestion_tenant_runs itr
                   JOIN ingestion_crawl_runs icr USING (run_id)
                   WHERE icr.orchestration_run_id=%s
                     AND itr.source='workday'
                     AND itr.status IN ('complete_nonzero','complete_zero')""",
                (orchestration_run_id,),
            )
            return {row[0] for row in cur.fetchall() if row[0]}
    finally:
        conn.close()


def run_batch(
    tenants: Sequence[str], orchestration_run_id: str, *, apply: bool
) -> None:
    env = os.environ.copy()
    env["WORKDAY_TENANT_FILTER"] = ",".join(tenants)
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
        "workday",
        "--orchestration-run-id",
        orchestration_run_id,
    ]
    if apply:
        command.append("--apply")
    subprocess.run(command, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
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

    inventory = unique_tenants(ingest_jobs._load_workday_list())
    completed = completed_tenants(args.orchestration_run_id) if args.apply else set()
    pending = [tenant for tenant in inventory if tenant not in completed]
    print(
        f"Workday checkpoint plan: inventory={len(inventory)} "
        f"completed={len(completed)} pending={len(pending)} batch_size={args.batch_size}",
        flush=True,
    )

    pending_batches = list(batches(pending, args.batch_size))
    for position, tenant_batch in enumerate(pending_batches, start=1):
        print(
            f"Workday checkpoint batch {position}/{len(pending_batches)}: "
            f"{','.join(tenant_batch)}",
            flush=True,
        )
        run_batch(tenant_batch, args.orchestration_run_id, apply=args.apply)
        if position < len(pending_batches) and args.batch_delay_seconds > 0:
            time.sleep(args.batch_delay_seconds)

    print("Workday checkpoint ingestion complete", flush=True)


if __name__ == "__main__":
    main()
