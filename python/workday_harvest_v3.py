#!/usr/bin/env python3
"""
workday_harvest_v3.py

Final comprehensive Workday sweep targeting:
- Fortune 1000 companies by name
- Specific industries: pharma, defense, energy, healthcare, finance
- Regional employers across all major US metros
- Companies known to use Workday but not yet in our list

Usage:
    python3 python/workday_harvest_v3.py
    python3 python/workday_harvest_v3.py --apply
"""

import os, re, time, logging, argparse, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
if not SERPER_API_KEY:
    raise ValueError("SERPER_API_KEY not set in .env")

SERPER_URL    = "https://google.serper.dev/search"
REQUEST_DELAY = 0.5

WD_URL_RE = re.compile(
    r'https://([a-zA-Z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com'
    r'(?:/[a-zA-Z_-]+)?/([a-zA-Z0-9_-]+)',
    re.IGNORECASE
)

TARGET_RE = re.compile(
    r'\b(data analyst|data engineer|analytics engineer|data scientist|'
    r'machine learning|ml engineer|ai engineer|business intelligence|'
    r'revenue operations|marketing analyst|product analyst|'
    r'financial analyst|data architect|mlops|quantitative|'
    r'data governance|data quality|people analytics|pricing analyst|'
    r'supply chain analyst|risk analyst|fraud analyst)\b',
    re.IGNORECASE
)

JUNK = {'job','jobs','en-us','en','external','apply','careers',
        'search','www','app','api','login','redirect'}

QUERIES = [
    # ── Named Fortune 500 companies known to use Workday ─────────────────────
    'site:myworkdayjobs.com "data" "johnson controls"',
    'site:myworkdayjobs.com "data engineer" "johnson johnson"',
    'site:myworkdayjobs.com "data scientist" "procter gamble"',
    'site:myworkdayjobs.com "data analyst" "general electric"',
    'site:myworkdayjobs.com "data" "honeywell"',
    'site:myworkdayjobs.com "data engineer" "3M"',
    'site:myworkdayjobs.com "data scientist" "raytheon"',
    'site:myworkdayjobs.com "data analyst" "lockheed martin"',
    'site:myworkdayjobs.com "data engineer" "general dynamics"',
    'site:myworkdayjobs.com "data scientist" "l3harris"',
    'site:myworkdayjobs.com "data analyst" "bae systems"',
    'site:myworkdayjobs.com "data engineer" "textron"',
    'site:myworkdayjobs.com "data scientist" "SAIC"',
    'site:myworkdayjobs.com "data analyst" "mantech"',
    'site:myworkdayjobs.com "data engineer" "peraton"',
    'site:myworkdayjobs.com "data" "caci international"',
    'site:myworkdayjobs.com "data scientist" "mitre"',
    'site:myworkdayjobs.com "data analyst" "charles schwab"',
    'site:myworkdayjobs.com "data engineer" "td ameritrade"',
    'site:myworkdayjobs.com "data scientist" "edward jones"',
    'site:myworkdayjobs.com "data analyst" "ameriprise"',
    'site:myworkdayjobs.com "data engineer" "principal financial"',
    'site:myworkdayjobs.com "data scientist" "lincoln financial"',
    'site:myworkdayjobs.com "data analyst" "sun life"',
    'site:myworkdayjobs.com "data engineer" "manulife"',
    'site:myworkdayjobs.com "data scientist" "metlife"',
    'site:myworkdayjobs.com "data analyst" "unum"',
    'site:myworkdayjobs.com "data engineer" "aetna"',
    'site:myworkdayjobs.com "data scientist" "elevance health"',
    'site:myworkdayjobs.com "data analyst" "centene"',
    'site:myworkdayjobs.com "data engineer" "molina healthcare"',
    'site:myworkdayjobs.com "data scientist" "anthem"',
    'site:myworkdayjobs.com "data analyst" "kaiser permanente"',
    'site:myworkdayjobs.com "data engineer" "cleveland clinic"',
    'site:myworkdayjobs.com "data scientist" "mayo clinic"',
    'site:myworkdayjobs.com "data analyst" "johns hopkins"',
    'site:myworkdayjobs.com "data engineer" "mass general brigham"',
    'site:myworkdayjobs.com "data scientist" "cedars sinai"',
    'site:myworkdayjobs.com "data analyst" "intermountain"',
    'site:myworkdayjobs.com "data engineer" "ascension"',
    'site:myworkdayjobs.com "data scientist" "dignity health"',
    'site:myworkdayjobs.com "data analyst" "commonspirit"',
    'site:myworkdayjobs.com "data engineer" "banner health"',
    'site:myworkdayjobs.com "data scientist" "advocate aurora"',
    'site:myworkdayjobs.com "data analyst" "providence health"',

    # ── Energy / Utilities ────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "exxon" OR "exxonmobil"',
    'site:myworkdayjobs.com "data scientist" "chevron"',
    'site:myworkdayjobs.com "data analyst" "conocophillips"',
    'site:myworkdayjobs.com "data engineer" "shell"',
    'site:myworkdayjobs.com "data scientist" "bp"',
    'site:myworkdayjobs.com "data analyst" "nextera energy"',
    'site:myworkdayjobs.com "data engineer" "duke energy"',
    'site:myworkdayjobs.com "data scientist" "dominion energy"',
    'site:myworkdayjobs.com "data analyst" "southern company"',
    'site:myworkdayjobs.com "data engineer" "entergy"',
    'site:myworkdayjobs.com "data scientist" "exelon"',
    'site:myworkdayjobs.com "data analyst" "ameren"',
    'site:myworkdayjobs.com "data engineer" "consolidated edison"',
    'site:myworkdayjobs.com "data scientist" "xcel energy"',
    'site:myworkdayjobs.com "data analyst" "eversource"',

    # ── Retail / Consumer ─────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "kroger"',
    'site:myworkdayjobs.com "data scientist" "costco"',
    'site:myworkdayjobs.com "data analyst" "home depot"',
    'site:myworkdayjobs.com "data engineer" "lowes"',
    'site:myworkdayjobs.com "data scientist" "best buy"',
    'site:myworkdayjobs.com "data analyst" "dollar general"',
    'site:myworkdayjobs.com "data engineer" "dollar tree"',
    'site:myworkdayjobs.com "data scientist" "tjx companies"',
    'site:myworkdayjobs.com "data analyst" "ross stores"',
    'site:myworkdayjobs.com "data engineer" "gap"',
    'site:myworkdayjobs.com "data scientist" "nordstrom"',
    'site:myworkdayjobs.com "data analyst" "macys"',
    'site:myworkdayjobs.com "data engineer" "publix"',
    'site:myworkdayjobs.com "data scientist" "albertsons"',
    'site:myworkdayjobs.com "data analyst" "sysco"',

    # ── Financial Services ────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "bank of america"',
    'site:myworkdayjobs.com "data scientist" "citigroup" OR "citi"',
    'site:myworkdayjobs.com "data analyst" "morgan stanley"',
    'site:myworkdayjobs.com "data engineer" "goldman sachs"',
    'site:myworkdayjobs.com "data scientist" "jpmorgan" OR "jp morgan"',
    'site:myworkdayjobs.com "data analyst" "blackrock"',
    'site:myworkdayjobs.com "data engineer" "state street"',
    'site:myworkdayjobs.com "data scientist" "northern trust"',
    'site:myworkdayjobs.com "data analyst" "raymond james"',
    'site:myworkdayjobs.com "data engineer" "stifel"',
    'site:myworkdayjobs.com "data scientist" "baird"',
    'site:myworkdayjobs.com "data analyst" "nuveen"',
    'site:myworkdayjobs.com "data engineer" "tiaa"',
    'site:myworkdayjobs.com "data scientist" "putnam investments"',
    'site:myworkdayjobs.com "data analyst" "invesco"',

    # ── Tech / SaaS ───────────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "oracle"',
    'site:myworkdayjobs.com "data scientist" "sap"',
    'site:myworkdayjobs.com "data analyst" "ibm"',
    'site:myworkdayjobs.com "data engineer" "accenture"',
    'site:myworkdayjobs.com "data scientist" "cognizant"',
    'site:myworkdayjobs.com "data analyst" "infosys"',
    'site:myworkdayjobs.com "data engineer" "tata consultancy"',
    'site:myworkdayjobs.com "data scientist" "wipro"',
    'site:myworkdayjobs.com "data analyst" "hcl technologies"',
    'site:myworkdayjobs.com "data engineer" "capgemini"',
    'site:myworkdayjobs.com "data scientist" "atos"',
    'site:myworkdayjobs.com "data analyst" "leidos"',
    'site:myworkdayjobs.com "data engineer" "unisys"',
    'site:myworkdayjobs.com "data scientist" "xerox"',
    'site:myworkdayjobs.com "data analyst" "pitney bowes"',

    # ── Pharma / Life Sciences ────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "astrazeneca"',
    'site:myworkdayjobs.com "data scientist" "novartis"',
    'site:myworkdayjobs.com "data analyst" "merck"',
    'site:myworkdayjobs.com "data engineer" "eli lilly"',
    'site:myworkdayjobs.com "data scientist" "abbvie"',
    'site:myworkdayjobs.com "data analyst" "bristol myers squibb"',
    'site:myworkdayjobs.com "data engineer" "johnson johnson"',
    'site:myworkdayjobs.com "data scientist" "bayer"',
    'site:myworkdayjobs.com "data analyst" "roche"',
    'site:myworkdayjobs.com "data engineer" "sanofi"',
    'site:myworkdayjobs.com "data scientist" "takeda"',
    'site:myworkdayjobs.com "data analyst" "boehringer ingelheim"',
    'site:myworkdayjobs.com "data engineer" "regeneron"',
    'site:myworkdayjobs.com "data scientist" "biogen"',
    'site:myworkdayjobs.com "data analyst" "vertex pharmaceuticals"',

    # ── Manufacturing / Industrial ────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "caterpillar"',
    'site:myworkdayjobs.com "data scientist" "deere" OR "john deere"',
    'site:myworkdayjobs.com "data analyst" "emerson electric"',
    'site:myworkdayjobs.com "data engineer" "parker hannifin"',
    'site:myworkdayjobs.com "data scientist" "illinois tool works"',
    'site:myworkdayjobs.com "data analyst" "eaton"',
    'site:myworkdayjobs.com "data engineer" "dover"',
    'site:myworkdayjobs.com "data scientist" "xylem"',
    'site:myworkdayjobs.com "data analyst" "roper technologies"',
    'site:myworkdayjobs.com "data engineer" "fortive"',
    'site:myworkdayjobs.com "data scientist" "danaher"',
    'site:myworkdayjobs.com "data analyst" "masco"',
    'site:myworkdayjobs.com "data engineer" "ball corporation"',
    'site:myworkdayjobs.com "data scientist" "sealed air"',
    'site:myworkdayjobs.com "data analyst" "sonoco"',

    # ── Media / Telecom ───────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "verizon"',
    'site:myworkdayjobs.com "data scientist" "att"',
    'site:myworkdayjobs.com "data analyst" "t-mobile"',
    'site:myworkdayjobs.com "data engineer" "charter communications"',
    'site:myworkdayjobs.com "data scientist" "comcast"',
    'site:myworkdayjobs.com "data analyst" "dish network"',
    'site:myworkdayjobs.com "data engineer" "fox corporation"',
    'site:myworkdayjobs.com "data scientist" "paramount"',
    'site:myworkdayjobs.com "data analyst" "nbcuniversal"',
    'site:myworkdayjobs.com "data engineer" "warner bros discovery"',
    'site:myworkdayjobs.com "data scientist" "iheartmedia"',
    'site:myworkdayjobs.com "data analyst" "hearst"',
    'site:myworkdayjobs.com "data engineer" "news corp"',
    'site:myworkdayjobs.com "data scientist" "ny times"',
    'site:myworkdayjobs.com "data analyst" "washington post"',

    # ── Transportation / Logistics ────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "fedex"',
    'site:myworkdayjobs.com "data scientist" "ups"',
    'site:myworkdayjobs.com "data analyst" "union pacific"',
    'site:myworkdayjobs.com "data engineer" "csx"',
    'site:myworkdayjobs.com "data scientist" "norfolk southern"',
    'site:myworkdayjobs.com "data analyst" "american airlines"',
    'site:myworkdayjobs.com "data engineer" "united airlines"',
    'site:myworkdayjobs.com "data scientist" "delta airlines"',
    'site:myworkdayjobs.com "data analyst" "southwest airlines"',
    'site:myworkdayjobs.com "data engineer" "xpo logistics"',
    'site:myworkdayjobs.com "data scientist" "ch robinson"',
    'site:myworkdayjobs.com "data analyst" "ryder"',
    'site:myworkdayjobs.com "data engineer" "j.b. hunt"',
    'site:myworkdayjobs.com "data scientist" "werner enterprises"',
    'site:myworkdayjobs.com "data analyst" "schneider national"',

    # ── Education ─────────────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "harvard university"',
    'site:myworkdayjobs.com "data scientist" "mit"',
    'site:myworkdayjobs.com "data analyst" "stanford university"',
    'site:myworkdayjobs.com "data engineer" "yale university"',
    'site:myworkdayjobs.com "data scientist" "columbia university"',
    'site:myworkdayjobs.com "data analyst" "university michigan"',
    'site:myworkdayjobs.com "data engineer" "university washington"',
    'site:myworkdayjobs.com "data scientist" "university texas"',
    'site:myworkdayjobs.com "data analyst" "ohio state"',
    'site:myworkdayjobs.com "data engineer" "penn state"',
    'site:myworkdayjobs.com "data scientist" "purdue university"',
    'site:myworkdayjobs.com "data analyst" "georgia tech"',
    'site:myworkdayjobs.com "data engineer" "carnegie mellon"',
    'site:myworkdayjobs.com "data scientist" "university illinois"',
    'site:myworkdayjobs.com "data analyst" "university minnesota"',
]


def serper_search(query: str) -> list:
    try:
        r = requests.post(SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 10}, timeout=10)
        return r.json().get("organic", []) if r.status_code == 200 else []
    except:
        return []


def parse_workday_url(url: str):
    m = WD_URL_RE.search(url)
    if not m:
        return None
    tenant = m.group(1).lower()
    server = m.group(2).lower()
    board  = m.group(3)
    if board.lower() in JUNK:
        parts = url.split('/')
        try:
            idx = next(i for i, p in enumerate(parts) if p.lower() in ('en-us','en-US'))
            board = parts[idx + 1] if idx + 1 < len(parts) else board
        except:
            pass
    return tenant, server, board


def validate_workday(tenant: str, server: str, board: str) -> tuple:
    url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        r = requests.post(url,
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "data"},
            headers=headers, timeout=12)
        if r.status_code != 200:
            return False, 0, ""
        data     = r.json()
        postings = data.get("jobPostings", [])
        active   = sum(1 for p in postings if TARGET_RE.search(p.get("title", "")))
        sample   = next((p["title"] for p in postings if TARGET_RE.search(p.get("title",""))), "")
        return True, active, sample
    except:
        return False, 0, ""


def guess_company_name(tenant: str) -> str:
    name = tenant
    for suffix in ['careers','jobs','external','corp','inc','llc','ltd','hr','us']:
        name = re.sub(rf'{suffix}$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    return name.replace('_',' ').replace('-',' ').strip().title()


def load_existing_tenants() -> set:
    path = Path(__file__).parent / "ingest_jobs.py"
    content = path.read_text()
    return set(re.findall(r'"([a-z0-9_-]+)",\s+"wd\d+"', content))


def main(apply: bool):
    existing = load_existing_tenants()
    log.info(f"Existing Workday tenants: {len(existing)}")

    found   = {}
    credits = 0

    for query in QUERIES:
        log.info(f"[{credits+1}/{len(QUERIES)}] {query[:70]}")
        for r in serper_search(query):
            url = r.get("link","") + " " + r.get("snippet","")
            for m in WD_URL_RE.finditer(url):
                tenant = m.group(1).lower()
                server = m.group(2).lower()
                board  = m.group(3)
                if tenant in JUNK or len(tenant) < 2:
                    continue
                if re.match(r'^\d+$', tenant):
                    continue
                if tenant not in existing and tenant not in found:
                    found[tenant] = (server, board)
        credits += 1
        time.sleep(REQUEST_DELAY)

    log.info(f"\nSearch complete — {len(found)} new tenants ({credits} credits)")
    log.info("Validating...")

    valid = []
    for i, (tenant, (server, board)) in enumerate(found.items()):
        works, active, sample = validate_workday(tenant, server, board)
        name   = guess_company_name(tenant)
        status = f"✅ {active} roles — {sample[:50]}" if works and active > 0 else ("⚠️  works, 0 roles" if works else "❌ failed")
        log.info(f"  [{i+1}/{len(found)}] {name} ({tenant}.{server}/{board}): {status}")
        if works:
            valid.append((name, tenant, board, server, active))
        time.sleep(REQUEST_DELAY)

    with_roles = [(n,t,b,s,a) for n,t,b,s,a in valid if a > 0]

    log.info(f"\n{'='*60}")
    log.info(f"Credits used:              {credits}")
    log.info(f"New tenants found:         {len(found)}")
    log.info(f"Valid boards:              {len(valid)}")
    log.info(f"With target roles:         {len(with_roles)}")

    if with_roles:
        log.info("\nNew companies to add:")
        for n,t,b,s,a in sorted(with_roles, key=lambda x: -x[4]):
            log.info(f"  ({a:3d} roles) (\"{n}\", \"{t}\", \"{b}\", \"{s}\"),")

    if apply and with_roles:
        path    = Path(__file__).parent / "ingest_jobs.py"
        content = path.read_text()
        new_lines = "\n    # Workday harvest v3 " + __import__('datetime').date.today().isoformat() + "\n"
        for n,t,b,s,a in sorted(with_roles, key=lambda x: -x[4]):
            new_lines += f"    (\"{n}\", \"{t}\", \"{b}\", \"{s}\"),\n"
        marker = "]\n\ndef fetch_workday_company"
        if marker in content:
            content = content.replace(marker, new_lines + marker)
            path.write_text(content)
            log.info(f"\n✅ Patched ingest_jobs.py with {len(with_roles)} new companies")
            log.info("Next: scp python/ingest_jobs.py root@208.68.38.249:/opt/job-market-analytics/python/ingest_jobs.py")
        else:
            log.warning("Insert marker not found — add manually from list above")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    main(apply=args.apply)
