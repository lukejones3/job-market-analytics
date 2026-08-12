import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from ingest_jobs import _parse_source_posted_date


def test_source_posted_dates_parse_explicit_ats_formats():
    assert _parse_source_posted_date("June  5, 2026") == "2026-06-05"
    assert _parse_source_posted_date("2026-08-12") == "2026-08-12"
    assert _parse_source_posted_date(1786495749) == "2026-08-12"


def test_source_posted_dates_do_not_invent_missing_values():
    assert _parse_source_posted_date(None) is None
    assert _parse_source_posted_date("not available") is None
