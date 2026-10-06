"""Coverage for Workday tenant/board expansion and mega-board partitioning.

Everything here runs offline: discovery merging, board enumeration order,
facet parsing/planning, server sharding, and the crawler's facet fallback
are pure or monkeypatched at the existing ``_wd_fetch_page`` seam.
"""
from __future__ import annotations

import asyncio

import ingest_jobs
import validate_workday_tenants as validator
from workday_boards import (
    board_company_id,
    board_key,
    board_token,
    candidate_boards,
    extract_location_facets,
    facet_subpartitions,
    merge_board_observations,
    order_tenants_by_server,
    plan_server_shards,
    primary_candidates,
)

# ---------------------------------------------------------------------------
# Discovery merging / sibling boards
# ---------------------------------------------------------------------------


def test_merge_keeps_sibling_boards_for_one_tenant():
    merged = merge_board_observations([
        {"tenant": "acme", "server": "wd5", "board": "External", "discovery_source": "commoncrawl"},
        {"tenant": "acme", "server": "wd5", "board": "Campus", "discovery_source": "commoncrawl"},
        {"tenant": "acme", "server": "wd5", "board": "External", "discovery_source": "commoncrawl"},
    ])
    assert [(c["tenant"], c["board"]) for c in merged] == [
        ("acme", "External"),
        ("acme", "Campus"),
    ]


def test_merge_dedups_case_insensitively_and_fills_missing_fields():
    merged = merge_board_observations([
        {"tenant": "Acme", "server": None, "board": None, "discovery_source": "edgar_probe"},
        {"tenant": "acme", "server": "wd5", "board": "External", "discovery_source": "commoncrawl"},
    ])
    # The boarded sighting supersedes the boardless seed for the same tenant.
    assert merged == [{
        "tenant": "acme", "server": "wd5", "board": "External",
        "company_name": None, "discovery_source": "commoncrawl",
    }]


def test_merge_keeps_boardless_seed_when_no_board_seen():
    merged = merge_board_observations([
        {"tenant": "beta", "server": "wd1", "board": None, "discovery_source": "edgar_probe"},
        {"tenant": "beta", "server": "wd1", "board": "en-US", "discovery_source": "commoncrawl"},
    ])
    assert len(merged) == 1
    assert merged[0]["board"] is None


def test_primary_candidates_prefers_boarded_sighting():
    primaries = primary_candidates([
        {"tenant": "acme", "server": "wd5", "board": None},
        {"tenant": "acme", "server": "wd5", "board": "External"},
        {"tenant": "beta", "server": "wd1", "board": "Careers"},
    ])
    assert [(c["tenant"], c["board"]) for c in primaries] == [
        ("acme", "External"),
        ("beta", "Careers"),
    ]


def test_board_key_normalizes_case_and_whitespace():
    assert board_key(" Acme ", "WD5", "External") == ("acme", "wd5", "external")


def test_candidate_boards_orders_seen_before_guessed_and_dedups():
    boards = candidate_boards(
        "acme",
        known_board="Campus",
        observed_boards=["External", "campus", "jobs"],
        common_boards=["External", "Careers"],
    )
    # "jobs" is a path segment, never a board name; "campus" dedups to Campus.
    assert boards[:3] == ["Campus", "External", "Careers"]
    assert boards.count("External") == 1
    lowered = [b.lower() for b in boards]
    assert len(lowered) == len(set(lowered))
    # Tenant-derived guesses come after everything actually seen.
    assert boards.index("acme_External") > boards.index("Careers")
    # Locale codes are never board candidates.
    assert "en-US" not in boards


def test_board_tokens_and_company_ids_are_distinct_per_board():
    external = board_token("acme", "wd5", "External")
    campus = board_token("acme", "wd5", "Campus")
    assert external == "acme/wd5/External"
    assert campus == "acme/wd5/Campus"
    assert board_company_id(external) != board_company_id(campus)
    # Stable across calls (integration must be idempotent).
    assert board_company_id(external) == board_company_id("acme/wd5/External")


# ---------------------------------------------------------------------------
# Validator board enumeration
# ---------------------------------------------------------------------------


def test_find_all_boards_returns_every_live_board(monkeypatch):
    totals = {"External": 120, "Careers": 45}
    monkeypatch.setattr(
        validator, "_try_board",
        lambda tenant, server, board: totals.get(board),
    )
    monkeypatch.setattr(validator.time, "sleep", lambda _s: None)
    found = validator._find_all_boards("acme", "wd5", None, observed_boards=["Careers"])
    # Careers was observed, so it is probed (and returned) before guessed boards.
    assert found == [("Careers", 45), ("External", 120)]


# ---------------------------------------------------------------------------
# Location facets
# ---------------------------------------------------------------------------


def test_extract_location_facets_from_cxs_payload():
    payload = {
        "facets": [
            {
                "facetParameter": "locationMainGroup",
                "values": [
                    {"id": "us-remote", "descriptor": "United States - Remote", "count": 40},
                    {"id": "us-ca", "descriptor": "United States - CA", "count": 120},
                    {"id": "in-blr", "descriptor": "India - Bangalore", "count": 300},
                ],
            },
            {
                "facetParameter": "workerSubType",
                "values": [{"id": "regular", "descriptor": "Regular", "count": 999}],
            },
        ]
    }
    facets = extract_location_facets(payload)
    assert facets == [
        ("locationMainGroup", "in-blr", 300),
        ("locationMainGroup", "us-ca", 120),
        ("locationMainGroup", "us-remote", 40),
    ]


def test_extract_location_facets_tolerates_other_shapes_and_junk():
    assert extract_location_facets(None) == []
    assert extract_location_facets({}) == []
    assert extract_location_facets({"facets": "nope"}) == []
    payload = {"facets": [{"name": "Job Location", "values": ["Austin", "Denver"]}]}
    assert extract_location_facets(payload) == [
        ("Job Location", "Austin", 0),
        ("Job Location", "Denver", 0),
    ]


def test_facet_subpartitions_only_for_oversized_terms():
    facets = [("locationMainGroup", "us-ca", 500), ("locationMainGroup", "us-tx", 300)]
    assert facet_subpartitions("data analyst", 500, 1900, facets) == []
    assert facet_subpartitions("data analyst", 5000, 1900, facets) == [
        ("data analyst", {"locationMainGroup": ["us-ca"]}),
        ("data analyst", {"locationMainGroup": ["us-tx"]}),
    ]
    assert facet_subpartitions("data analyst", 5000, 1900, []) == []


# ---------------------------------------------------------------------------
# Crawler partitioning with facets
# ---------------------------------------------------------------------------


def test_detailed_query_pages_reports_partition_total_and_stays_in_window(monkeypatch):
    calls = []

    async def fake_page(session, url, headers, offset, limit, search_text=""):
        calls.append(offset)
        return ([{"externalPath": f"/job/{offset}"}], 9999, 200)

    monkeypatch.setattr(ingest_jobs, "_wd_fetch_page", fake_page)
    postings, total, status = asyncio.run(
        ingest_jobs._wd_fetch_query_pages_detailed(None, "url", {}, "engineer", 20)
    )
    assert status == 200
    assert total == 9999
    assert postings
    assert max(calls) < ingest_jobs._WD_SAFE_RESULT_WINDOW


def test_facet_partition_passes_applied_facets_through_fetch(monkeypatch):
    """A facet sub-partition must reach the wire as appliedFacets, capped at
    the reliable window like every other partition."""
    facet_calls = []

    async def fake_page(session, url, headers, offset, limit, search_text="", applied_facets=None):
        facet_calls.append((search_text, applied_facets, offset))
        return ([{"externalPath": "/job/facet-only"}], 20, 200)

    monkeypatch.setattr(ingest_jobs, "_wd_fetch_page", fake_page)
    postings, total, status = asyncio.run(
        ingest_jobs._wd_fetch_query_pages_detailed(
            None, "url", {}, "engineer", 20, {"locationMainGroup": ["us"]}
        )
    )
    assert status == 200 and total == 20
    assert postings == [{"externalPath": "/job/facet-only"}]
    assert facet_calls
    assert all(call[1] == {"locationMainGroup": ["us"]} for call in facet_calls)
    assert all(call[0] == "engineer" for call in facet_calls)


# ---------------------------------------------------------------------------
# Server sharding
# ---------------------------------------------------------------------------


def test_plan_server_shards_spreads_hosts_across_shards():
    rows = (
        [("A", f"a{i}", "External", "wd5") for i in range(4)]
        + [("B", f"b{i}", "External", "wd1") for i in range(4)]
    )
    shards = plan_server_shards(rows, max_tenants_per_shard=4)
    assert len(shards) == 2
    for shard in shards:
        servers = {row[3] for row in shard}
        assert servers == {"wd1", "wd5"}
    flat = [row[1] for shard in shards for row in shard]
    assert sorted(flat) == sorted([r[1] for r in rows])


def test_order_tenants_by_server_collapses_boards_and_interleaves():
    rows = [
        ("Acme", "acme", "External", "wd5"),
        ("Acme Campus", "acme", "Campus", "wd5"),
        ("Beta", "beta", "External", "wd5"),
        ("Core", "core", "External", "wd1"),
        ("Data", "data", "External", "wd1"),
    ]
    ordered = order_tenants_by_server(rows)
    assert sorted(ordered) == ["acme", "beta", "core", "data"]
    # First two tenants come from different servers.
    server_of = {"acme": "wd5", "beta": "wd5", "core": "wd1", "data": "wd1"}
    assert server_of[ordered[0]] != server_of[ordered[1]]
