import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from company_identity import is_plausible_company_name, resolved_workday_company_name


def test_role_titles_are_not_admitted_as_company_names():
    for value in ("Data Engineer", "Senior Data Engineer", "Lead Data Engineer",
                  "Sustainability Data Analyst"):
        assert not is_plausible_company_name(value)


def test_real_company_names_are_admitted():
    for value in ("Sysco", "CoStar Group", "State of North Carolina", "Hyve Solutions"):
        assert is_plausible_company_name(value)


def test_known_workday_tenant_repairs_bad_discovery_label():
    assert resolved_workday_company_name("sysco", "Data Engineer") == "Sysco"
    assert resolved_workday_company_name("costar", "Senior Data Engineer") == "CoStar Group"


def test_unknown_bad_label_falls_back_to_tenant():
    assert resolved_workday_company_name("example-corp", "Data Analyst") == "Example Corp"
