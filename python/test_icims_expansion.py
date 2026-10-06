"""iCIMS expansion: Common Crawl seed mining + harvester-consistent validation.

Pins three contracts:
- Common Crawl URLs become tenant slugs only when they are real
  career-board URLs ({slug}.icims.com/jobs/...), never infra subdomains.
- Mined seeds stage into ats_tenants_candidates as 'pending' and never
  write discovered_companies directly.
- validate_icims counts what the harvester would admit (harvester card
  parser + normalize_location foreign drop + shared target-role gate),
  so 'active' still requires >=1 US target role.
"""
import json
import types

import discover_icims_commoncrawl as miner
import validate_ats_candidates as validator


# ------------------------------------------------------------ URL parsing

def test_parse_plain_tenant():
    assert miner.parse_icims_tenant("https://kp.icims.com/jobs/search?pr=0") == "kp"


def test_parse_careers_prefixed_tenant():
    url = "https://careers-example.icims.com/jobs/4812/data-engineer/job?in_iframe=1"
    assert miner.parse_icims_tenant(url) == "careers-example"


def test_parse_is_case_insensitive():
    assert miner.parse_icims_tenant("https://KP.icims.com/jobs/search") == "kp"


def test_parse_rejects_infra_subdomains():
    for slug in ("api-foo", "login", "analytics-eu", "us123", "eu2",
                 "notacustomer9", "statuspage", "marketplace"):
        assert miner.parse_icims_tenant(f"https://{slug}.icims.com/jobs/x") is None, slug


def test_parse_rejects_noise_subdomains():
    assert miner.parse_icims_tenant("https://www.icims.com/jobs/x") is None


def test_parse_requires_jobs_path():
    assert miner.parse_icims_tenant("https://kp.icims.com/") is None
    assert miner.parse_icims_tenant("https://kp.icims.com/about-us") is None


def test_humanize_company_name():
    assert miner.humanize_company_name("careers-acme-health") == "Acme Health"
    assert miner.humanize_company_name("kp") == "Kp"


# ------------------------------------------------------- Common Crawl fetch

class _Resp:
    def __init__(self, text="", payload=None, status=200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _cdx_lines(*urls):
    return "\n".join(json.dumps({"url": u}) for u in urls)


def test_fetch_dedups_filters_and_respects_known(monkeypatch):
    cdx = _cdx_lines(
        "https://kp.icims.com/jobs/search",                       # known tenant
        "https://newhealth.icims.com/jobs/12/data-analyst/job",   # new
        "https://newhealth.icims.com/jobs/search",                # dup of new
        "https://api-x.icims.com/jobs/search",                    # infra
        "https://www.icims.com/jobs/search",                      # noise
        "https://bigretail.icims.com/about",                      # no /jobs/ path
        "https://bigretail.icims.com/jobs/intro",                 # new
    )

    def fake_get(url, params=None, timeout=None):
        if "collinfo" in url:
            return _Resp(payload=[{"id": "CC-TEST-1"}])
        return _Resp(text=cdx)

    monkeypatch.setattr(miner.requests, "get", fake_get)
    out = miner.fetch_commoncrawl_tenants({"kp"}, crawls=1)
    assert [c["tenant"] for c in out] == ["newhealth", "bigretail"]
    assert out[0]["company_name"] == "Newhealth"
    assert all(c["discovery_source"] == "commoncrawl" for c in out)


def test_fetch_respects_limit(monkeypatch):
    cdx = _cdx_lines(*[f"https://t{i}.icims.com/jobs/search" for i in range(10)])

    def fake_get(url, params=None, timeout=None):
        if "collinfo" in url:
            return _Resp(payload=[{"id": "CC-TEST-1"}])
        return _Resp(text=cdx)

    monkeypatch.setattr(miner.requests, "get", fake_get)
    out = miner.fetch_commoncrawl_tenants(set(), crawls=1, limit=3)
    assert len(out) == 3


# --------------------------------------------------------- candidate staging

class _FakeCursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def close(self):
        pass


def test_save_candidates_stages_pending_never_registry(monkeypatch):
    cur = _FakeCursor()
    conn = types.SimpleNamespace(cursor=lambda: cur, commit=lambda: None,
                                 close=lambda: None)
    monkeypatch.setattr(miner, "get_conn", lambda: conn)
    inserted = miner.save_candidates(
        [{"tenant": "newhealth", "company_name": "Newhealth",
          "discovery_source": "commoncrawl"}], apply=True)
    assert inserted == 1
    sql, params = cur.executed[0]
    normalized = " ".join(sql.split())
    assert "INSERT INTO ats_tenants_candidates" in normalized
    assert "'pending'" in normalized
    assert "'commoncrawl'" in normalized
    assert "discovered_companies" not in normalized
    assert params == ("newhealth", "Newhealth")


def test_save_candidates_dry_run_writes_nothing(monkeypatch):
    def boom():
        raise AssertionError("dry-run must not open a DB connection")
    monkeypatch.setattr(miner, "get_conn", boom)
    assert miner.save_candidates([{"tenant": "x"}], apply=False) == 0


# ------------------------------------------------------------- validator

_CARD = """
<li class="iCIMS_JobCardItem">
  <span class="sr-only field-label">Job Locations</span><span>{loc}</span>
  <a href="/jobs/{jid}/slug/job?in_iframe=1">
    <span class="sr-only field-label">Requisition Title</span>
    <h3>{title}</h3>
  </a>
</li>
"""


def _board(*cards):
    return "<html><body><ul>" + "".join(cards) + "</ul></body></html>"


def _mock_validator_requests(monkeypatch, search_html, intro_html=None, status=200):
    def fake_get(url, params=None, timeout=None, headers=None, allow_redirects=True):
        if "/jobs/search" in url:
            return _Resp(text=search_html, status=status)
        if "/jobs/intro" in url and intro_html is not None:
            return _Resp(text=intro_html, status=200)
        return _Resp(text="", status=404)
    monkeypatch.setattr(validator.requests, "get", fake_get)


def test_validate_icims_us_target_activates(monkeypatch):
    html = _board(
        _CARD.format(jid=1, title="Data Scientist", loc="Seattle, WA"),
        _CARD.format(jid=2, title="Registered Nurse", loc="Seattle, WA"),
    )
    _mock_validator_requests(monkeypatch, html)
    server, us, target, status_ = validator.validate_icims({"tenant": "acme"})
    assert (server, us, target, status_) == (None, 2, 1, "active")


def test_validate_icims_clinical_board_is_no_data(monkeypatch):
    # iCIMS skews healthcare: a board full of clinical titles is alive
    # and US, but contributes zero target roles and must not activate.
    html = _board(
        _CARD.format(jid=1, title="Registered Nurse", loc="Dallas, TX"),
        _CARD.format(jid=2, title="Physical Therapist", loc="Dallas, TX"),
        _CARD.format(jid=3, title="Medical Assistant", loc="Dallas, TX"),
    )
    _mock_validator_requests(monkeypatch, html)
    _server, us, target, status_ = validator.validate_icims({"tenant": "acme"})
    assert (us, target, status_) == (3, 0, "no_data_jobs")


def test_validate_icims_foreign_board_not_counted(monkeypatch):
    html = _board(_CARD.format(jid=1, title="Data Analyst", loc="Toronto, Canada"))
    _mock_validator_requests(monkeypatch, html)
    _server, us, target, status_ = validator.validate_icims({"tenant": "acme"})
    assert (us, target) == (0, 0)


def test_validate_icims_dead_portal_unreachable(monkeypatch):
    _mock_validator_requests(monkeypatch, "", status=404)
    assert validator.validate_icims({"tenant": "ghost"}) == (None, 0, 0, "unreachable")


def test_validate_icims_live_js_portal_stays_measurable(monkeypatch):
    # Search returns 200 with no server-rendered cards (JS portal): the
    # tenant is alive, so it stays no_data_jobs instead of unreachable.
    _mock_validator_requests(monkeypatch, "<html><body>icims portal</body></html>")
    _server, _us, target, status_ = validator.validate_icims({"tenant": "acme"})
    assert (target, status_) == (0, "no_data_jobs")
