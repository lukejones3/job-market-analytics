"""Pure regressions for ATS discovery and Workday locator handling."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discover_ats_aggressive as discovery
import validate_ats_candidates as validation
from bamboohr_harvest import _is_us


def test_discovery_urls():
    text = " ".join([
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Seattle/123",
        "https://widgets.bamboohr.com/careers/42",
        "https://jobs.jobvite.com/rocket/job/abc",
    ])
    found = set(discovery._extract_ats_from_text(text))
    assert ("workday", "acme", "wd5/External") in found
    assert ("bamboohr", "widgets", None) in found
    assert ("jobvite", "rocket", None) in found


def test_workday_locator_is_split_before_resolution():
    calls = {}
    original_resolve = validation._wd_resolve_server
    original_board = validation._wd_find_board
    original_count = validation._wd_count_jobs
    try:
        validation._wd_resolve_server = lambda tenant, server: calls.setdefault("server", server) or server
        validation._wd_find_board = lambda tenant, server, board: calls.setdefault("board", board) or board
        validation._wd_count_jobs = lambda tenant, server, board: (12, 3)
        locator, us_jobs, target_jobs, status = validation.validate_workday(
            {"tenant": "acme", "server": "wd5/External"})
        assert calls == {"server": "wd5", "board": "External"}
        assert (locator, us_jobs, target_jobs, status) == ("wd5/External", 12, 3, "active")
    finally:
        validation._wd_resolve_server = original_resolve
        validation._wd_find_board = original_board
        validation._wd_count_jobs = original_count


def test_bamboohr_location_filter():
    assert _is_us({"location": {"state": "WA", "addressCountry": "United States"}}, False)
    # Remote is a workplace type, not evidence that a global role accepts US applicants.
    assert not _is_us({"location": {"state": "Ontario", "addressCountry": "Canada"}}, True)
    assert not _is_us({"location": {"state": "Ontario", "addressCountry": "Canada"}}, False)


if __name__ == "__main__":
    test_discovery_urls()
    test_workday_locator_is_split_before_resolution()
    test_bamboohr_location_filter()
    print("ATS reliability regressions: 3/3")
