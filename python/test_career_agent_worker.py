import importlib.util
from pathlib import Path

MODULE = Path(__file__).with_name("career_agent_worker.py")
spec = importlib.util.spec_from_file_location("career_agent_worker", MODULE)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def test_queries_include_independent_web_search():
    queries = worker.search_queries({"roleFamilies": ["data engineering"], "locations": ["Seattle"], "remoteAllowed": True})
    assert any("linkedin.com/in" in query for query in queries)
    assert any("staffing" in query for query in queries)
    assert any("remote" in query.lower() for query in queries)


def test_web_only_recruiter_can_rank_without_lander_job():
    score = worker.score_contact({"title": "Data Engineering Recruiter", "specialty": "Python and data", "evidence": "Recruiting remote data engineers", "location": "Seattle", "firm": "Example Staffing", "source_kind": "web", "openings": [], "linkedin_url": "https://linkedin.com/in/example"}, {"roleFamilies": ["data engineering"], "skills": ["python"], "locations": ["Seattle"]})
    assert score >= 70


def test_merge_marks_cross_source_contact():
    base = {"full_name": "A Recruiter", "firm": "Firm", "linkedin_url": "https://linkedin.com/in/a", "evidence_urls": ["a"], "openings": []}
    merged = worker.merge_contacts([{**base, "source_kind": "lander"}], [{**base, "evidence_urls": ["b"]}])
    assert merged[0]["source_kind"] == "both"
    assert merged[0]["evidence_urls"] == ["a", "b"]


def test_selection_reserves_space_for_independent_recruiters():
    lander = [{"full_name": f"Manager {i}", "firm": "Company", "source_kind": "lander", "score": 90 - i} for i in range(20)]
    web = [{"full_name": f"Recruiter {i}", "firm": "Search Firm", "source_kind": "web", "score": 60 - i} for i in range(8)]
    selected = worker.select_contacts(lander + web, 20)
    assert sum(item["source_kind"] == "web" for item in selected) == 8


def test_recruiter_filter_does_not_admit_department_heads():
    allowed = r"recruit|talent acquisition|talent partner|staffing|sourc|people partner|human resources"
    assert worker.re.search(allowed, "Senior Technical Recruiter", worker.re.I)
    assert worker.re.search(allowed, "Talent Acquisition Partner", worker.re.I)
    assert not worker.re.search(allowed, "Head of Data", worker.re.I)
    assert not worker.re.search(allowed, "Director of AI", worker.re.I)
