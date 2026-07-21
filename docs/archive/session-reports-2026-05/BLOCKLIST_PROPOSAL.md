# Company Blocklist Proposal

Generated: 2026-05-13

---

## Task 1: Audit Results

Raw data snapshot (top 50 by volume, status=raw, data_tier=1):

| Company | Jobs | Salary% | Recent% | Non-US% | Verdict |
|---|---|---|---|---|---|
| Speechify | 1418 | 21.9% | 100% | — | **city-spam (parallel dedup running)** |
| Anduril Industries | 724 | 85.8% | 100% | — | Real employer ✓ |
| **cgsfederal** | 569 | 99.5% | **2.6%** | 0% | **BLOCK — federal staffing aggregator** |
| SpaceX | 534 | 67.6% | 97.2% | — | Real employer ✓ |
| **Invisible Agency** | 518 | 53.1% | **8.1%** | 0% | **BLOCK — content/AI training aggregator** |
| OpenAI | 484 | 0.2% | 28.1% | — | Real employer ✓ (salary scraping issue) |
| **Accenture Federal Services** | 366 | 82% | 89.9% | 0% | **GRAY — real consulting firm, but staffing-adjacent** |
| Veeva | 348 | 64.4% | 9.2% | — | Real employer, low recent (normal for pharma SaaS) ✓ |
| Databricks | 279 | 93.5% | 33% | — | Real employer ✓ |
| Anthropic | 244 | 86.9% | 50.8% | — | Real employer ✓ |
| **Bosch Group** | 221 | **13.1%** | 52% | 0.9% | **GRAY — manufacturing/hardware, low salary signal** |
| Roblox | 219 | 90% | 86.3% | — | Real employer ✓ |
| Toast | 210 | 95.7% | 70% | — | Real employer ✓ |
| **DEPT®** | 153 | 18.3% | 62.7% | **99.3%** | **BLOCK — Netherlands digital agency, almost zero US jobs** |
| **Canonical** | 153 | 0% | **7.8%** | **94.8%** | **BLOCK — UK/Ubuntu company, 95% non-US, nearly all stale** |
| **Jobs for Humanity** | 149 | 20.8% | **8.1%** | 3.4% | **BLOCK — job board aggregator** |
| **NXP Semiconductors** | 144 | 0% | **8.3%** | **87.5%** | **BLOCK — Dutch semiconductor, 88% non-US** |
| **CapTech Consulting** | 126 | 19.8% | 37.3% | 0% | **GRAY — 25 unique roles × ~8 cities = geo-spam pattern** |
| Archer56 | 121 | 76% | 100% | "97%" | Real employer ✓ (Archer Aviation eVTOL — loc_country bug, all US) |
| **Devoteam** | 119 | **0%** | **6.7%** | 0% | **BLOCK — EU IT consulting, all stale, zero salary** |
| **Cermaticom** | 162 | 0% | **13.6%** | "0%" | **BLOCK — Indonesian fintech (Cermati.com), all Jakarta jobs mislabeled as US** |
| **Relx** | 114 | 6.1% | 28.9% | **81.6%** | **BLOCK — UK information group, 82% non-US** |
| **Dexcom** | 110 | 3.6% | 20% | **78.2%** | **GRAY — US med-device but 78% non-US, mostly stale** |
| Handshake | 116 | 0% | 31.9% | 35.3% | Real employer ✓ (salary parse gap) |
| binance | 164 | 0% | 20.7% | 31.7% | Real employer, borderline (crypto, some stale) |

---

## Task 2 & 3: Proposed Blocklist

### `ABSOLUTE_BLOCK` — never ingest

| Company | Reason |
|---|---|
| `cgsfederal` | Federal staffing aggregator (CGS Federal). Posts on behalf of DoD/agencies. 569 jobs, only 2.6% recent — bulk historical dump, not a real employer. |
| `Invisible Agency` | Content/AI training data aggregator. 518 jobs = 518 unique roles (pure aggregation). 8% recent. |
| `Cermaticom` | Indonesian fintech (cermati.com). Every single job is in Jakarta but loc_country='US' (normalizer bug). Wrong country, wrong market. |
| `Jobs for Humanity` | Job board/aggregator. 149 jobs, 147 unique roles, 8% recent. Aggregates across many employers. |
| `Devoteam` | European IT consulting firm. 0% salary, 6.7% recent, oldest 2023. Stale aggregation dump. |
| `DEPT®` | Dutch digital agency. 153 jobs, **99.3% non-US**. Near-zero US presence. |
| `Canonical` | UK-based (Ubuntu). 153 jobs, 94.8% non-US, 7.8% recent. Wrong market + mostly stale. |
| `NXP Semiconductors` | Dutch semiconductor. 144 jobs, 87.5% non-US, 8.3% recent. Wrong market + stale. |
| `Relx` | UK information group. 114 jobs, 81.6% non-US. Wrong market. |

### `GRAY_AREA` — your call

| Company | Jobs | Issue | My take |
|---|---|---|---|
| **Accenture Federal Services** | 366 | Consulting/staffing firm (Accenture subsidiary). Real employer but hires into client-embedded gov roles. High volume, mostly recent, 82% salary. | Block if you want zero staffing firms; keep if you count them as a legitimate data/tech employer |
| **CapTech Consulting** | 126 | 25 unique roles × ~8 cities = classic "always hiring everywhere" consulting geo-spam. Roles are real (Data Engineer, ML Engineer). | Probably keep but could add a dedup note |
| **Bosch Group** | 221 | Real company but manufacturing/hardware focus, 13% salary. Not really the target market. | Block (wrong domain) or keep (some data roles) |
| **Dexcom** | 110 | US medical device company, but 78% of postings are non-US. Some US data/analytics roles. | Could add a `loc_country='US'` filter at query time instead of blanket block |
| **binance** | 164 | Real crypto exchange, 31.7% non-US, 20.7% recent. Crypto focus. | Keep (borderline but real employer) or block (crypto/non-US) |

---

## Proposed Implementation (pending your approval)

**File:** `python/company_blocklist.py`

Hook location in `ingest_jobs.py`: top of `ingest_job()` at line 1386, right before the title filter:

```python
if is_company_blocked(job.company):
    return False
```

For existing rows: `UPDATE job_postings SET status='ignored' WHERE company_id IN (...)` — one query per blocked company, easy to reverse.

---

## What I need from you

1. Approve/modify the `ABSOLUTE_BLOCK` list above (9 companies)
2. Decision on each of the 5 gray-area companies
3. Then I'll build `company_blocklist.py` and wire it into `ingest_jobs.py`

No DB changes until you confirm.
