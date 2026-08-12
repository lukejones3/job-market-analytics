import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from location_normalizer import normalize_location


def test_accented_and_unlisted_foreign_cities_fail_closed():
    for value in (
        "Montréal",
        "Ljubljana",
        "Cebu",
        "Da Nang",
        "Taoyuan",
        "Ciudad de México",
        "Villeneuve d'Ascq",
        "SRB - Novi Sad",
        "Rockstar Dundee",
    ):
        assert normalize_location(value).country == "foreign", value


def test_remote_requires_positive_us_evidence():
    assert normalize_location(None, "remote").country == "unknown"
    assert normalize_location("Remote", "remote").country == "unknown"
    assert normalize_location("Distributed", "remote").country == "unknown"
    assert normalize_location("Remote, United States", "remote").country == "US"
    assert normalize_location("Remote, Canada", "remote").country == "foreign"


def test_explicit_us_locations_remain_eligible():
    assert normalize_location("Seattle, WA").country == "US"
    assert normalize_location("United States").country == "US"
