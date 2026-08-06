"""Regression tests for evidence-first experience classification."""

from experience_level_v3 import classify_experience, extract_years


def check(title, description, expected):
    result = classify_experience(title, description)
    assert result.level == expected, (title, result.evidence())
    return result


def test_workiva_sales_data_analyst_is_associate():
    result = check(
        "Sales Data Analyst",
        "Minimum Qualifications Undergraduate degree and a minimum of 2 years of related experience. "
        "Preferred Qualifications Present insightful visuals to senior leadership and stakeholders.",
        "associate",
    )
    assert result.required_years == 2
    assert "senior" not in result.title_signal


def test_stakeholder_language_does_not_create_seniority():
    check("Data Analyst", "Requirements 1 year of experience. Present results to senior leadership.", "entry")


def test_explicit_titles_win_but_conflict_is_visible():
    result = check("Senior Data Analyst", "Requirements 2 years of related experience.", "senior")
    assert result.conflicts == ["title_senior_vs_required_associate"]
    check("Senior Associate, Analytics", "Requirements 3 years of experience.", "senior")
    check("Associate Director, Data", "Requirements 4 years of experience.", "senior")


def test_four_level_year_boundaries():
    check("Data Analyst", "Minimum Qualifications 0 years of experience.", "entry")
    check("Data Analyst", "Requirements 1 year of relevant experience.", "entry")
    check("Data Analyst", "Basic Qualifications 2+ years of professional experience.", "associate")
    check("Data Analyst", "Required: 3-5 years of related experience.", "mid")
    check("Data Analyst", "What you'll need 5 years of relevant experience.", "senior")
    check("Data Analyst", "Requirements You have 3+ years working with SQL and Tableau.", "mid")
    check("Data Analyst", "Qualifications 2 years using reporting and BI tools.", "associate")


def test_title_levels_and_associate():
    check("Associate Data Analyst", "", "associate")
    check("Data Engineer I", "", "associate")
    check("Data Engineer II", "", "mid")
    check("Data Engineer III", "", "senior")
    check("Entry-Level Data Analyst", "", "entry")


def test_preferred_experience_never_promotes_required_level():
    result = check(
        "Data Analyst",
        "Minimum Qualifications 2 years of related experience. Preferred Qualifications 5 years of experience.",
        "associate",
    )
    assert result.required_years == 2
    assert result.preferred_years == 5


def test_ambiguous_jobs_abstain():
    result = check("Data Analyst", "Build dashboards and partner with teams.", "unknown")
    assert result.confidence < 0.5


def test_company_age_is_not_candidate_experience():
    required, preferred = extract_years("For over 25 years we have built software. Build dashboards for customers.")
    assert required is None and preferred is None


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("experience v3 tests passed")
