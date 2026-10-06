"""Tests for Phenom / Avature / SuccessFactors career-host recipes.

Dependency-light: no database, no network. Follows the Oracle unblock
suite's approach — parsers and gates are exercised against fixtures, and
crawlers are driven with the network seams monkeypatched.
"""
from __future__ import annotations

import json

import pytest

import career_host_engine as engine
from career_host_engine import (
    _avature_detail_urls,
    _host_source,
    _phenom_detail_url,
    _phenom_listings_page,
    _recipe_platform,
    _sf_tile_urls,
    _sf_xml_urls,
    fingerprint_page,
    fingerprint_url,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HOST = {"host_id": "CH-ENT", "company_name": "Acme", "status": "shadow"}


def _posting(*, org: str = "Acme", country: str = "US", title: str = "Senior Data Engineer") -> dict:
    us = country.upper() in {"US", "USA", "UNITED STATES"}
    return {
        "@type": "JobPosting",
        "title": title,
        "description": "Build data pipelines. " * 12,
        "hiringOrganization": {"@type": "Organization", "name": org},
        "jobLocation": [{
            "@type": "Place",
            "address": {"@type": "PostalAddress",
                        "addressLocality": "Chicago" if us else "Bengaluru",
                        "addressRegion": "IL" if us else "KA", "addressCountry": country},
        }],
        "datePosted": "2026-10-01",
        "identifier": {"@type": "PropertyValue", "value": "REQ-1"},
    }


def _fake_detail(posting):
    def fake(url):
        unique = dict(posting)
        unique["identifier"] = {"@type": "PropertyValue", "value": f"REQ-{abs(hash(url))}"}
        unique["url"] = url
        return url, [unique], None
    return fake


class _FakeResponse:
    def __init__(self, text: str = "", payload: dict | None = None):
        self.text = text
        self._payload = payload or {}
        self.content = text.encode()
        self.headers: dict = {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, payload: dict):
        self.payload = payload
        self.posts: list = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakeResponse(payload=self.payload)


# ---------------------------------------------------------------------------
# Fingerprinting -> api_recipe routing
# ---------------------------------------------------------------------------

def test_avature_net_host_routes_to_api_recipe() -> None:
    fp = fingerprint_url("https://synopsys.avature.net/careers/SearchJobs")
    assert fp and fp.platform == "avature" and fp.strategy == "api_recipe"
    assert fp.recipe["platform"] == "avature"


def test_branded_searchjobs_path_is_avature_shaped() -> None:
    fp = fingerprint_url("https://careers.example.com/en_US/careers/SearchJobs?jobOffset=0")
    assert fp and fp.platform == "avature" and fp.strategy == "api_recipe"


def test_phenompeople_host_routes_to_api_recipe_with_prefix() -> None:
    fp = fingerprint_url("https://jobs.ascension.org/us/en/search-results")
    assert fp is None  # not a phenom-named host; page-marker path claims it

    fp = fingerprint_url("https://acme.phenompeople.com/us/en/jobs")
    assert fp and fp.platform == "phenom" and fp.strategy == "api_recipe"
    assert fp.recipe["url_prefix"] == "us/en"
    assert fp.recipe["origin"] == "https://acme.phenompeople.com"


def test_successfactors_host_routes_to_rmk_recipe() -> None:
    fp = fingerprint_url("https://career4.successfactors.com/career?company=acme")
    assert fp and fp.platform == "successfactors" and fp.strategy == "api_recipe"
    assert fp.recipe["platform"] == "successfactors"
    assert fp.recipe["mode"] == "rmk_tiles"
    assert fp.tenant_token == "acme"


def test_successfactors_legacy_feed_routes_to_xml_recipe() -> None:
    url = ("https://career4.successfactors.com/career?company=acme"
           "&career_ns=job_listing&resultType=XML")
    fp = fingerprint_url(url)
    assert fp and fp.platform == "successfactors"
    assert fp.recipe["mode"] == "xml_feed"
    assert fp.recipe["company"] == "acme"


def test_branded_page_markers_claim_phenom_and_successfactors() -> None:
    phenom_html = '<script src="https://cdn.phenompeople.com/widgets.js"></script>'
    fp = fingerprint_page("https://jobs.example-health.org/careers", phenom_html)
    assert fp.platform == "phenom" and fp.strategy == "api_recipe"
    assert fp.recipe["platform"] == "phenom"

    sf_html = '<div data-url="/tile-search-results/?startrow=0"></div>'
    fp = fingerprint_page("https://jobs.example-industrial.com/search/", sf_html)
    assert fp.platform == "successfactors" and fp.strategy == "api_recipe"
    assert fp.recipe["mode"] == "rmk_tiles"

    assert fingerprint_page("https://example.com/careers", "<html></html>").platform == "custom"


# ---------------------------------------------------------------------------
# Recipe dispatch
# ---------------------------------------------------------------------------

def _host(platform: str, **overrides) -> dict:
    host = {"extraction_strategy": "api_recipe", "platform": platform,
            "careers_url": "https://careers.example.com/", **overrides}
    return host


def test_recipe_platform_dispatch_and_sources() -> None:
    for platform in ("phenom", "avature", "successfactors"):
        host = _host(platform, api_recipe={"platform": platform, "origin": "https://x.example"})
        assert _recipe_platform(host) == platform
        # Enterprise jobs stay career_site rows; only Oracle gets its own source.
        assert _host_source(host) == "career_site"

    # JSON-text recipes (as RealDictCursor can deliver jsonb) dispatch too.
    host = _host("phenom", api_recipe=json.dumps({"platform": "phenom"}))
    assert _recipe_platform(host) == "phenom"

    # Platform named on the host also works when recipe is minimal.
    assert _recipe_platform(_host("avature", api_recipe={})) == "avature"

    # Generic hosts do not dispatch.
    assert _recipe_platform({"extraction_strategy": "sitemap_jsonld",
                             "platform": "phenom", "api_recipe": {}}) is None
    assert _recipe_platform(_host("custom", api_recipe={"platform": ""})) is None


# ---------------------------------------------------------------------------
# Phenom
# ---------------------------------------------------------------------------

def test_phenom_listing_page_parses_refine_search() -> None:
    payload = {"refineSearch": {"status": 200, "totalHits": 2, "data": {"jobs": [
        {"jobId": "457323", "title": "Senior Data Engineer", "location": "Bartlett, Illinois, 60103"},
        {"jobId": "457324", "title": "Data Scientist", "location": "Chicago, Illinois"},
    ]}}}
    session = _FakeSession(payload)
    jobs, total = _phenom_listings_page(session, "https://jobs.example.org", {}, 100, 0)
    assert total == 2
    assert [row["jobId"] for row in jobs] == ["457323", "457324"]
    url, kwargs = session.posts[0]
    assert url == "https://jobs.example.org/widgets"
    assert kwargs["json"]["ddoKey"] == "refineSearch"
    assert kwargs["json"]["jobs"] is True


def test_phenom_detail_url_uses_site_prefix() -> None:
    assert _phenom_detail_url("https://jobs.example.org", "us/en", "42") == \
        "https://jobs.example.org/us/en/job/42"


def test_phenom_crawl_applies_shared_gates(monkeypatch) -> None:
    host = dict(HOST, careers_url="https://jobs.example.org/us/en/search-results",
                jobs_host="jobs.example.org",
                extraction_strategy="api_recipe", platform="phenom",
                api_recipe={"platform": "phenom", "origin": "https://jobs.example.org",
                            "url_prefix": "us/en"})
    listings = ([{"jobId": "1", "title": "Senior Data Engineer"},
                 {"jobId": "2", "title": "Warehouse Associate"}], 2)
    monkeypatch.setattr(engine, "_phenom_listings_page", lambda *a, **k: listings)
    monkeypatch.setattr(engine, "_fetch_jobposting_page", _fake_detail(_posting()))

    jobs, stats, detail = engine.crawl_phenom_host(host, max_pages=50, workers=2)
    assert len(jobs) == 1
    assert jobs[0].company == "Acme"
    assert jobs[0].metadata["source_quality_status"] == "quarantine"
    assert detail["reported_total"] == 2


def test_phenom_crawl_rejects_foreign_and_mismatched_detail(monkeypatch) -> None:
    host = dict(HOST, careers_url="https://jobs.example.org/us/en/",
                jobs_host="jobs.example.org",
                extraction_strategy="api_recipe", platform="phenom",
                api_recipe={"platform": "phenom", "origin": "https://jobs.example.org"})
    monkeypatch.setattr(
        engine, "_phenom_listings_page",
        lambda *a, **k: ([{"jobId": "9", "title": "Senior Data Engineer"}], 1))

    monkeypatch.setattr(engine, "_fetch_jobposting_page",
                        _fake_detail(_posting(country="IN")))
    jobs, stats, _ = engine.crawl_phenom_host(host, max_pages=10, workers=1)
    assert jobs == []
    assert stats.foreign_rejections == 1

    # A different named employer on the host's own domain is a brand
    # attribution (subsidiary), never silently assigned to the host.
    monkeypatch.setattr(engine, "_fetch_jobposting_page",
                        _fake_detail(_posting(org="Contoso Health")))
    jobs, stats, _ = engine.crawl_phenom_host(host, max_pages=10, workers=1)
    assert len(jobs) == 1
    assert jobs[0].company == "Contoso Health"
    assert stats.brand_attributed_jobs == 1
    assert stats.identity_mismatches == 0

    # No stated organization at all is an identity mismatch.
    monkeypatch.setattr(engine, "_fetch_jobposting_page",
                        _fake_detail(_posting(org="")))
    jobs, stats, _ = engine.crawl_phenom_host(host, max_pages=10, workers=1)
    assert jobs == []
    assert stats.identity_mismatches == 1


# ---------------------------------------------------------------------------
# Avature
# ---------------------------------------------------------------------------

def test_avature_detail_urls_from_searchjobs_html() -> None:
    html = """
    <a href="/careers/JobDetail/Data-Engineer/123">Data Engineer</a>
    <a href="https://acme.avature.net/careers/FolderDetail/x/9">Folder</a>
    <a href="/careers/SearchJobs?jobOffset=6">Next</a>
    """
    urls = _avature_detail_urls(html, "https://acme.avature.net/careers/SearchJobs")
    assert urls == [
        "https://acme.avature.net/careers/JobDetail/Data-Engineer/123",
        "https://acme.avature.net/careers/FolderDetail/x/9",
    ]


def test_avature_crawl_paginates_and_gates(monkeypatch) -> None:
    host = dict(HOST, careers_url="https://acme.avature.net/careers",
                jobs_host="acme.avature.net",
                extraction_strategy="api_recipe", platform="avature",
                api_recipe={"platform": "avature", "origin": "https://acme.avature.net"})
    pages = {
        0: '<a href="/careers/JobDetail/a/1">a</a><a href="/careers/JobDetail/b/2">b</a>',
        6: "",
    }

    def fake_get(session, url, **kwargs):
        offset = int(url.rsplit("jobOffset=", 1)[1])
        return _FakeResponse(text=pages.get(offset, ""))

    monkeypatch.setattr(engine, "_safe_get", fake_get)
    monkeypatch.setattr(engine, "_fetch_jobposting_page", _fake_detail(_posting()))

    jobs, stats, detail = engine.crawl_avature_host(host, max_pages=50, workers=2)
    assert len(jobs) == 2
    assert {job.metadata["source_quality_status"] for job in jobs} == {"quarantine"}
    assert detail["recipe"] == "searchjobs"


# ---------------------------------------------------------------------------
# SuccessFactors
# ---------------------------------------------------------------------------

def test_sf_tiles_from_rmk_fragment() -> None:
    html = """
    <ul>
      <li class="job-tile job-id-101"><a href="/job/Data-Engineer/101">Data Engineer</a></li>
      <li class="job-tile job-id-102"><a href="https://jobs.example.com/job/Analyst/102">Analyst</a></li>
    </ul>
    """
    urls = _sf_tile_urls(html, "https://jobs.example.com/tile-search-results/?startrow=0")
    assert urls == ["https://jobs.example.com/job/Data-Engineer/101",
                    "https://jobs.example.com/job/Analyst/102"]


def test_sf_urls_from_legacy_xml_feed() -> None:
    xml = b"""<?xml version="1.0"?>
    <jobs><job><title>Data Engineer</title>
      <joburl>https://career4.successfactors.com/career?company=acme&amp;job=1</joburl></job>
    <job><title>Analyst</title><url>https://career4.successfactors.com/career?company=acme&amp;job=2</url></job></jobs>
    """
    urls = _sf_xml_urls(xml)
    assert len(urls) == 2
    assert all(url.startswith("https://career4.successfactors.com/") for url in urls)


def test_sf_rmk_crawl_paginates_tiles_and_gates(monkeypatch) -> None:
    host = dict(HOST, careers_url="https://jobs.example.com/search/",
                jobs_host="jobs.example.com",
                extraction_strategy="api_recipe", platform="successfactors",
                api_recipe={"platform": "successfactors",
                            "origin": "https://jobs.example.com", "mode": "rmk_tiles"})
    pages = {
        "0": '<li class="job-tile job-id-1"><a href="/job/a/1">a</a></li>',
        "1": '<li class="job-tile job-id-2"><a href="/job/b/2">b</a></li>',
        "2": "",
    }

    def fake_get(session, url, **kwargs):
        startrow = url.rsplit("startrow=", 1)[1]
        return _FakeResponse(text=pages.get(startrow, ""))

    monkeypatch.setattr(engine, "_safe_get", fake_get)
    monkeypatch.setattr(engine, "_fetch_jobposting_page", _fake_detail(_posting()))

    jobs, stats, detail = engine.crawl_successfactors_host(host, max_pages=50, workers=2)
    assert len(jobs) == 2
    assert detail["successfactors_mode"] == "rmk_tiles"


def test_sf_xml_feed_crawl(monkeypatch) -> None:
    host = dict(HOST, careers_url="https://career4.successfactors.com/career?company=acme&career_ns=job_listing",
                jobs_host="career4.successfactors.com",
                extraction_strategy="api_recipe", platform="successfactors",
                api_recipe={"platform": "successfactors", "mode": "xml_feed",
                            "feed_url": "https://career4.successfactors.com/feed.xml"})
    xml = (b'<?xml version="1.0"?><jobs><job><title>Data Engineer</title>'
           b'<joburl>https://career4.successfactors.com/job/1</joburl></job></jobs>')
    monkeypatch.setattr(engine, "_safe_get", lambda session, url, **k: _FakeResponse(text=xml.decode()))
    monkeypatch.setattr(engine, "_fetch_jobposting_page", _fake_detail(_posting()))

    jobs, stats, detail = engine.crawl_successfactors_host(host, max_pages=10, workers=1)
    assert len(jobs) == 1
    assert detail["successfactors_mode"] == "xml_feed"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
