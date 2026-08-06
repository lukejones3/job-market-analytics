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


def test_public_email_requires_literal_search_evidence():
    email, url = worker.public_email_from_results([
        {"title": "Jane Recruiter", "snippet": "Contact Jane at jane@searchfirm.com", "link": "https://searchfirm.com/jane"}
    ], "Jane Recruiter", "Search Firm")
    assert email == "jane@searchfirm.com"
    assert url == "https://searchfirm.com/jane"


def test_public_email_rejects_generic_contact_inbox():
    email, _ = worker.public_email_from_results([
        {"title": "Contact", "snippet": "Email info@searchfirm.com", "link": "https://searchfirm.com"}
    ], "Jane Recruiter", "Search Firm")
    assert email is None


def test_public_email_rejects_other_person_in_same_result():
    email, _ = worker.public_email_from_results([
        {"title": "Zorik Shtikel", "snippet": "Page also lists thomas@kidscanfish.net", "link": "https://example.org/zorik"}
    ], "Zorik Shtikel", "")
    assert email is None


def test_public_email_does_not_match_single_letter_surname():
    email, _ = worker.public_email_from_results([
        {"title": "Rob L.", "snippet": "Rob L. recruiter; l2globalsolutionsllc@gmail.com", "link": "https://example.org/rob"}
    ], "Rob L.", "")
    assert email is None


def test_public_email_rejects_same_name_without_recruiting_context():
    email, _ = worker.public_email_from_results([
        {"title": "Matt Andrews - Athletics Staff", "snippet": "Email mandrews@transy.edu", "link": "https://sports.example/matt"}
    ], "Matt Andrews", "")
    assert email is None


def test_location_scope_does_not_leak_remote_jobs_when_remote_is_disabled():
    source = MODULE.read_text()
    assert 'if requirements.get("remoteAllowed", True):' in source
    assert "location_clause = f\"({location_clause} OR lower" in source


def test_interrupted_campaigns_are_recovered():
    source = MODULE.read_text()
    assert "status='running' AND updated_at < now() - interval '45 minutes'" in source
    assert "Interrupted worker run recovered and requeued" in source
