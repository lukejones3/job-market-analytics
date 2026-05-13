"""
company_blocklist.py

Permanent blocklist for companies that should never be ingested.
Checked at ingest time in ingest_job() and by the /jobs feed query.

To block a new company:
  1. Add to ABSOLUTE_BLOCK below with a comment explaining why.
  2. Run: UPDATE job_postings SET status='ignored' WHERE company_id = _md5_id('C', '<name>');

Categories:
  ABSOLUTE_BLOCK  — aggregators, staffing firms, wrong-market companies.
                    Existing rows are set to status='ignored'.
"""

# Exact company_name strings as they appear in the companies table.
# Case-insensitive match applied at ingest time.
ABSOLUTE_BLOCK: set[str] = {
    # Federal staffing aggregator — posts on behalf of DoD/agencies, not a real employer.
    # 569 jobs, only 2.6% recent.
    "cgsfederal",

    # Content/AI training data aggregator — 518 unique jobs = pure aggregation, 8% recent.
    "Invisible Agency",

    # Indonesian fintech (cermati.com) — all jobs are in Jakarta, loc_country='US' bug.
    # Wrong country, wrong market entirely.
    "Cermaticom",

    # Job board/aggregator — 149 jobs across 147 unique roles, only 8% recent.
    "Jobs for Humanity",

    # European IT consulting — 0% salary, 6.7% recent, stale dump going back to 2023.
    "Devoteam",

    # Dutch digital agency — 153 jobs, 99.3% non-US. Near-zero US presence.
    "DEPT®",

    # UK/Ubuntu company — 153 jobs, 94.8% non-US, 7.8% recent. Wrong market + stale.
    "Canonical",

    # Dutch semiconductor — 144 jobs, 87.5% non-US, 8.3% recent. Wrong market + stale.
    "NXP Semiconductors",

    # UK information analytics group — 114 jobs, 81.6% non-US. Wrong market.
    "Relx",

    # Accenture subsidiary for US government — consulting/staffing-adjacent,
    # hires into client-embedded roles rather than direct product teams.
    "Accenture Federal Services",

    # Manufacturing/hardware focus — 221 jobs, 13.1% salary disclosure, wrong domain.
    "Bosch Group",

    # German fashion e-commerce (Hamburg) — 51.5% of jobs in non-US cities, loc_country='US'
    # is a location_normalizer bug (German cities falling back to 'US'). Wrong market.
    "ABOUT YOU SE & Co. KG",

    # German car rental — 43.6% non-US city, same loc_country normalizer bug. Wrong market.
    "SIXT",

    # German fintech (Scalable Capital) — 41.4% non-US city, same normalizer bug. Wrong market.
    "Scalablegmbh",
}

# Lowercased version for fast case-insensitive lookup.
_BLOCK_LOWER: set[str] = {name.lower() for name in ABSOLUTE_BLOCK}


def is_company_blocked(company_name: str | None) -> bool:
    """Return True if this company should be skipped at ingest time."""
    if not company_name:
        return False
    return company_name.lower() in _BLOCK_LOWER
