"""Shared, dependency-free helpers for Workday board expansion.

Three growth problems live outside the crawler itself:

1. Sibling boards. One Workday tenant can expose several CXS boards
   (``External``, ``External_Career_Site``, a campus board, ...). Discovery
   used to keep only the first board seen per tenant, so every sibling was
   silently discarded before validation ever ran.
2. Mega-board partitioning. Boards above the reliable CXS result window are
   partitioned by the 65-term role vocabulary, but a single term can still
   exceed the window on Walmart-scale boards. Location facets returned by
   CXS itself are the second partitioning axis.
3. Crawl sharding. Tenants sharing a ``wd{N}`` host share its rate limits, so
   a crawl plan should spread hosts across batches instead of hammering one
   server with a whole batch.

Nothing here touches the network or the DB, so all of it is unit-testable.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Board-name patterns tried during validation, most frequent first. Kept in
# step with validate_workday_tenants.COMMON_BOARDS.
DEFAULT_COMMON_BOARDS: Tuple[str, ...] = (
    "External",
    "Careers",
    "External_Career_Site",
    "JobSearch",
    "Global",
    "Search",
    "US",
    "CareerSite",
)

# Locale codes appear as the first path segment on localized board URLs; they
# are never board names.
LOCALE_CODES = frozenset({
    "en-us", "en-gb", "en-ca", "en-au", "fr-ca", "fr-fr",
    "de-de", "es-es", "ja-jp", "zh-cn", "pt-br", "nl-nl",
    "ko-kr", "it-it", "sv-se", "pl-pl", "ru-ru",
})

_NON_BOARD_SEGMENTS = frozenset({"job", "jobs", "apply", "wday", "cxs", "en"})

# A single employer rarely exposes more distinct location facet values than
# this in a useful way; the cap keeps partition counts (and therefore 429
# exposure) bounded on mega-boards.
MAX_LOCATION_FACETS = 25


def valid_board_name(board: Optional[str]) -> bool:
    """Whether a URL path segment can plausibly be a CXS board name."""
    if not board:
        return False
    board = board.strip()
    if len(board) < 2 or len(board) > 80:
        return False
    lowered = board.lower()
    return lowered not in LOCALE_CODES and lowered not in _NON_BOARD_SEGMENTS


def board_key(tenant: str, server: Optional[str], board: Optional[str]) -> Tuple[str, str, str]:
    """Case-insensitive dedup key for one (tenant, server, board) locator."""
    return (
        (tenant or "").strip().lower(),
        (server or "").strip().lower(),
        (board or "").strip().lower(),
    )


def merge_board_observations(observations: Iterable[Dict]) -> List[Dict]:
    """Collapse raw discovery sightings into one candidate per board.

    Every sighting is a dict with ``tenant``/``server``/``board``/
    ``company_name``/``discovery_source`` keys (any may be missing). Sightings
    of distinct sibling boards for the same tenant all survive. A boardless
    sighting (tenant known, board unknown) survives only when no boarded
    sighting exists for that tenant: validation probes the boarded tenant
    anyway, so the seed adds nothing.

    First-seen display values win, except that a missing server/board may be
    filled in by a later sighting of the same locator.
    """
    merged: Dict[Tuple[str, str, str], Dict] = {}
    order: List[Tuple[str, str, str]] = []
    for obs in observations:
        tenant = (obs.get("tenant") or "").strip().lower()
        if len(tenant) < 2:
            continue
        board = (obs.get("board") or "").strip() or None
        if board is not None and not valid_board_name(board):
            board = None
        server = (obs.get("server") or "").strip().lower() or None
        key = board_key(tenant, server, board)
        if key not in merged:
            merged[key] = {
                "tenant": tenant,
                "server": server,
                "board": board,
                "company_name": obs.get("company_name"),
                "discovery_source": obs.get("discovery_source") or "unknown",
            }
            order.append(key)
            continue
        existing = merged[key]
        existing["server"] = existing["server"] or server
        existing["board"] = existing["board"] or board
        existing["company_name"] = existing["company_name"] or obs.get("company_name")

    boarded_tenants = {key[0] for key in order if merged[key]["board"]}
    return [
        merged[key]
        for key in order
        if merged[key]["board"] or key[0] not in boarded_tenants
    ]


def primary_candidates(candidates: Sequence[Dict]) -> List[Dict]:
    """One validation-queue candidate per tenant, boarded sightings first.

    ``workday_tenants_candidates`` is keyed by tenant; this picks the row that
    represents the tenant there while sibling boards live in
    ``workday_tenant_boards``.
    """
    by_tenant: Dict[str, Dict] = {}
    for candidate in candidates:
        tenant = candidate["tenant"]
        current = by_tenant.get(tenant)
        if current is None or (not current.get("board") and candidate.get("board")):
            by_tenant[tenant] = candidate
    return list(by_tenant.values())


def candidate_boards(
    tenant: str,
    known_board: Optional[str] = None,
    observed_boards: Sequence[str] = (),
    common_boards: Sequence[str] = DEFAULT_COMMON_BOARDS,
) -> List[str]:
    """Ordered, deduped board names worth probing for one tenant.

    Order matters: boards actually seen in the wild (known, then discovered
    siblings) are probed before guessed patterns, and probing stops being
    useful long before the derived-name tail, so callers may cap the result.
    """
    tenant = (tenant or "").strip()
    titled = tenant.capitalize()
    ordered: List[str] = []
    seen: set[str] = set()

    def add(board: Optional[str]) -> None:
        if board and valid_board_name(board) and board.lower() not in seen:
            seen.add(board.lower())
            ordered.append(board)

    add(known_board)
    for board in observed_boards:
        add(board)
    for board in common_boards:
        add(board)
    for board in (
        f"{tenant}_External", f"{titled}_External",
        f"{tenant}Careers", f"{titled}Careers",
        f"{tenant}_Careers", f"{titled}_Careers",
        f"{tenant}Jobs", f"{titled}Jobs",
    ):
        add(board)
    return ordered


def board_token(tenant: str, server: str, board: str) -> str:
    """The ``discovered_companies.board_token`` locator for one board."""
    return f"{tenant}/{server}/{board}"


def board_company_id(token: str) -> str:
    """Stable company_id for a board token (matches validate_workday_tenants)."""
    return "WD" + hashlib.md5(f"workday|{token}".encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Location-facet partitioning
# ---------------------------------------------------------------------------

def extract_location_facets(
    payload: Optional[dict],
    max_facets: int = MAX_LOCATION_FACETS,
) -> List[Tuple[str, str, int]]:
    """Pull location facet (parameter, value_id, count) triples from CXS JSON.

    CXS responses carry a ``facets`` array whose entries name a
    ``facetParameter`` (for example ``locationMainGroup``) and list the values
    observed on the board. Only location facets are useful as a US-yield
    partitioning axis. Parsing is deliberately defensive about the exact
    payload shape: anything unrecognized yields no facets and the caller
    falls back to term-only partitioning.
    """
    if not isinstance(payload, dict):
        return []
    facets = payload.get("facets")
    if not isinstance(facets, list):
        return []

    found: List[Tuple[str, str, int]] = []
    for facet in facets:
        if not isinstance(facet, dict):
            continue
        parameter = (
            facet.get("facetParameter") or facet.get("parameter")
            or facet.get("name") or facet.get("id") or ""
        )
        if "location" not in str(parameter).lower():
            continue
        values = facet.get("values") or facet.get("facetValues") or []
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                value_id = value.get("id") or value.get("value") or value.get("descriptor")
                try:
                    count = int(value.get("count") or 0)
                except (TypeError, ValueError):
                    count = 0
            else:
                value_id, count = value, 0
            if value_id:
                found.append((str(parameter), str(value_id), count))

    found.sort(key=lambda item: (-item[2], item[1]))
    # Deduplicate identical (parameter, value) pairs, keeping the best count.
    deduped: List[Tuple[str, str, int]] = []
    seen: set[Tuple[str, str]] = set()
    for parameter, value_id, count in found:
        if (parameter, value_id) not in seen:
            seen.add((parameter, value_id))
            deduped.append((parameter, value_id, count))
    return deduped[:max_facets]


def facet_subpartitions(
    search_text: str,
    term_total: int,
    window: int,
    location_facets: Sequence[Tuple[str, str, int]],
) -> List[Tuple[str, Dict[str, List[str]]]]:
    """Second-axis partitions for one term that still exceeds the window.

    Returns ``(search_text, appliedFacets)`` pairs, one per location facet
    value. Empty when the term already fits inside the reliable window or no
    facets are available: facet partitioning is strictly additive recovery
    for postings the term partition cannot reach.
    """
    if term_total <= window or not location_facets:
        return []
    return [
        (search_text, {parameter: [value_id]})
        for parameter, value_id, _count in location_facets
    ]


# ---------------------------------------------------------------------------
# Crawl sharding by wd server
# ---------------------------------------------------------------------------

def plan_server_shards(
    rows: Sequence[Sequence[str]],
    max_tenants_per_shard: int,
) -> List[List[Sequence[str]]]:
    """Group (name, tenant, board, wd_server) rows into host-spread shards.

    Tenants on the same ``wd{N}`` host share rate limits, so each shard is
    filled round-robin across servers: a batch touches many hosts lightly
    rather than one host exclusively. Deterministic for a given input.
    """
    if max_tenants_per_shard < 1:
        raise ValueError("max_tenants_per_shard must be positive")
    by_server: Dict[str, List[Sequence[str]]] = {}
    for row in rows:
        server = str(row[3]).strip().lower() if len(row) > 3 else ""
        by_server.setdefault(server, []).append(row)
    # Interleave tenants across servers, largest server first for stability.
    interleaved: List[Sequence[str]] = []
    queues = [by_server[server] for server in sorted(by_server, key=lambda s: -len(by_server[s]))]
    while any(queues):
        for queue in queues:
            if queue:
                interleaved.append(queue.pop(0))
    return [
        interleaved[start:start + max_tenants_per_shard]
        for start in range(0, len(interleaved), max_tenants_per_shard)
    ]


def order_tenants_by_server(rows: Sequence[Sequence[str]]) -> List[str]:
    """Unique tenant slugs ordered to spread wd servers across batches.

    Used by the resumable Workday wrapper: tenants are collapsed across
    boards (the ingestor filter selects by tenant), then interleaved by
    server so every sequential batch spans several hosts.
    """
    tenant_server: Dict[str, str] = {}
    for row in rows:
        tenant = str(row[1]).strip().lower()
        server = str(row[3]).strip().lower() if len(row) > 3 else ""
        if tenant and tenant not in tenant_server:
            tenant_server[tenant] = server
    shards = plan_server_shards(
        [(tenant, tenant, "", server) for tenant, server in tenant_server.items()],
        max_tenants_per_shard=max(len(tenant_server), 1),
    )
    if not shards:
        return []
    return [str(row[1]) for row in shards[0]]


_SERVER_RE = re.compile(r"^wd\d+$")


def is_wd_server(value: Optional[str]) -> bool:
    return bool(value and _SERVER_RE.match(value.strip().lower()))
