"""In-process tenant outcome recorder shared by source harvesters.

Tenant completion must be based on attempted boards, not rows that survived role
and location filters. Harvesters record a terminal outcome even when a healthy
board has zero accepted jobs; expiry consumes only successful tenant outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class TenantOutcome:
    source: str
    crawl_tenant: str
    status: str
    jobs_fetched: int = 0
    errors: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


_lock = Lock()
_outcomes: dict[tuple[str, str], TenantOutcome] = {}


def reset() -> None:
    with _lock:
        _outcomes.clear()


def _merge(current: TenantOutcome | None, incoming: TenantOutcome) -> TenantOutcome:
    if current is None:
        return incoming
    statuses = {current.status, incoming.status}
    if "partial_failure" in statuses or ("failed" in statuses and len(statuses) > 1):
        status = "partial_failure"
    elif statuses == {"failed"}:
        status = "failed"
    else:
        total = max(current.jobs_fetched, incoming.jobs_fetched)
        status = "complete_nonzero" if total else "complete_zero"
    return TenantOutcome(
        source=incoming.source,
        crawl_tenant=incoming.crawl_tenant,
        status=status,
        jobs_fetched=max(current.jobs_fetched, incoming.jobs_fetched),
        errors=current.errors + incoming.errors,
        detail={**current.detail, **incoming.detail},
    )


def record_success(source: str, crawl_tenant: str, jobs_fetched: int, **detail: Any) -> None:
    if not crawl_tenant:
        return
    count = max(0, int(jobs_fetched))
    incoming = TenantOutcome(
        source=source,
        crawl_tenant=str(crawl_tenant),
        status="complete_nonzero" if count else "complete_zero",
        jobs_fetched=count,
        detail=detail,
    )
    with _lock:
        key = (source, str(crawl_tenant))
        _outcomes[key] = _merge(_outcomes.get(key), incoming)


def record_failure(source: str, crawl_tenant: str, error: str, *, partial: bool = False, **detail: Any) -> None:
    if not crawl_tenant:
        return
    incoming = TenantOutcome(
        source=source,
        crawl_tenant=str(crawl_tenant),
        status="partial_failure" if partial else "failed",
        errors=1,
        detail={"error": str(error)[:500], **detail},
    )
    with _lock:
        key = (source, str(crawl_tenant))
        _outcomes[key] = _merge(_outcomes.get(key), incoming)


def snapshot(source: str | None = None) -> list[TenantOutcome]:
    with _lock:
        values = list(_outcomes.values())
    if source is not None:
        values = [outcome for outcome in values if outcome.source == source]
    return sorted(values, key=lambda outcome: (outcome.source, outcome.crawl_tenant))
