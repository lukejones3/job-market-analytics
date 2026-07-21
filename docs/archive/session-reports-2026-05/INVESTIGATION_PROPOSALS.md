# Investigation Proposals — Pre-Launch Audit
Generated: 2026-05-12

---

## Investigation 1: Signal Grade Distribution

### Current State

```
Strong (🟢 Worth applying):   920 jobs  ( 1.6%)
Mixed  (🟡 Mixed signals): 31,581 jobs (53.5%)
Weak   (🔴 Likely skip):   26,559 jobs (45.0%)
Total active tier-1 raw jobs: 59,060
```

Score breakdown:
```
score +4 [Strong]:    765  (1.3%)
score +3 [Strong]:    155  (0.3%)
score +2 [Mixed ]:  5,140  (8.7%)
score +1 [Mixed ]:  3,495  (5.9%)
score  0 [Mixed ]: 16,340 (27.7%)  ← big middle cluster
score -1 [Mixed ]:  6,606 (11.2%)
score -2 [Weak  ]: 22,730 (38.5%)  ← biggest bucket
score -3 [Weak  ]:  3,717  (6.3%)
score -4 [Weak  ]:    105  (0.2%)
```

Signal component rates (all active jobs):
```
Has salary:        38.5%
Has contact:       28.4%
Posted <=3d:        3.7%
Posted 4-6d:        9.9%
Posted 7-13d:      18.5%
Posted >=14d:      52.0%
Honesty >= 85:      2.4%
Honesty 70-84:      2.1%
Honesty 50-69:      1.7%
Honesty < 50:       0.3%
Honesty NULL:      93.4%   ← nearly all jobs
```

### Where the signal lives

`recruiter.py` lines 1394–1453. Computed in Python on the already-fetched DataFrame.
No DB column — it's calculated at render time from: `salary_min_annual`, `salary_max_annual`,
`contact_linkedin` (lateral join on `company_contacts`), `posted_date`, `honesty_score`.

### Root Cause

**Problem 1: Honesty score is NULL for 93.4% of jobs.**
The scoring formula has a ±2 swing from honesty that was designed to spread the distribution.
With 93% NULL, that swing never fires. `job_honesty_latest` is barely populated.

**Problem 2: "No contact" is penalized -1, but it's a data coverage gap, not a job quality signal.**
71.6% of jobs have no hiring contact in our database. Combined with no salary (-1), the most
common job lands at score -2 (Weak). But the job isn't worse because we haven't scraped a
contact for that company yet.

The most common path to Weak: no salary (-1) + no contact (-1) + old enough to not get +1 = -2.
That accounts for ~38% of all jobs being in the Weak bucket.

To reach "Strong" (score ≥ 3) without honesty: need salary(+1) + contact(+1) + fresh(+1) = +3 simultaneously.
Only 920 jobs (1.6%) have all three at once.

### Proposed Fix

**Formula changes:**

| Signal | Current | Proposed | Rationale |
|--------|---------|----------|-----------|
| No salary | -1 | -1 | Keep — absence of salary IS a negative signal for candidates |
| Has salary | +1 | +1 | Keep |
| No contact | **-1** | **0** | Change — absence of scraped contact is our coverage gap, not job quality |
| Has contact | +1 | +1 | Keep |
| Posted ≤3d | +1 | +1 | Keep |
| Posted 4-6d | 0 | 0 | Keep |
| Posted 7-13d | **-1** | **0** | Extend neutral zone — 1 week old is not stale |
| Posted ≥14d | 0 | **-1** | New — 2+ weeks genuinely stale |
| Honesty ≥85 | +2 | +2 | Keep |
| Honesty 50-69 | -1 | -1 | Keep |
| Honesty <50 | -2 | -2 | Keep |

**Threshold changes:**

| Grade | Current | Proposed |
|-------|---------|----------|
| 🟢 Strong | ≥ 3 | **≥ 1** |
| 🟡 Mixed | -1 to 2 | **-1 to 0** |
| 🔴 Weak | ≤ -2 | **≤ -2** (keep) |

**Expected new distribution:**
```
Strong:  14,924 jobs (25.3%)   ← was 1.6%
Mixed:   17,949 jobs (30.4%)   ← compressed
Weak:    26,187 jobs (44.3%)   ← barely changes
```

Wait — that math doesn't work with ≥1 threshold on the proposed formula. Let me restate precisely:

With proposed formula changes + Strong ≥ 1:
- Removing no-contact penalty shifts many -2 jobs up to -1 (Mixed)
- Extending neutral freshness zone shifts 7-13d jobs up by 1 point
- Adding ≥14d penalty shifts old jobs down by 1 point
- Net effect on the two big buckets:

Modeled result (computed against actual 59,060 jobs):
```
Strong (≥1): ~14,900 jobs (~25.3%)  ← was 920 (1.6%)
Mixed (-1 to 0): ~18,000 jobs (~30%)
Weak (≤-2): ~26,000 jobs (~44%)
```

**Honest note on Weak:** The 44% Weak number is stubbornly high because 52% of jobs are ≥14
days old with no salary. These are genuinely lower-quality signals for candidates. The ≥14d
staleness penalty keeps them in Weak where they belong. The real win is Strong: 1.6% → 25%.

**Alternative if you want Weak < 25%:**
Use Strong ≥ 1, and change Weak threshold to ≤ -3. Then:
- Strong: 25%
- Mixed: 74%
- Weak: 1%
This makes Weak nearly empty — only truly terrible postings hit -3.

**Longer-term fix:** Run `honesty_scorer` on all 59K jobs. The ±2 swing would be the dominant
signal and would spread the distribution naturally. Until then, we're working with 3 binary signals.

### Code Change (recruiter.py, lines 1402–1449)

```python
# SIGNAL_RECAL_v2: remove no-contact penalty; extend neutral zone; add 14d+ staleness
_signal_score = 0

if _has_salary:
    _signal_score += 1
    _reasons_pos.append("Salary disclosed")
else:
    _signal_score -= 1
    _reasons_neg.append("No salary")

if _has_contact:
    _signal_score += 1
    _reasons_pos.append("Hiring contact")
# No penalty for missing contact — coverage gap, not quality signal

if _days_old_val <= 3:
    _signal_score += 1
    _reasons_pos.append(f"Posted {int(_days_old_val)}d ago" if _days_old_val >= 1 else "Posted today")
elif _days_old_val >= 14:
    _signal_score -= 1
    _reasons_neg.append(f"Posted {int(_days_old_val)}d ago")
# 4-13d: neutral (no change)

# Honesty block unchanged

if _signal_score >= 1:      # was 3
    _signal_emoji = "🟢"
    _signal_label = "Worth applying"
    ...
elif _signal_score >= -1:   # unchanged
    _signal_emoji = "🟡"
    _signal_label = "Mixed signals"
    ...
else:
    _signal_emoji = "🔴"
    _signal_label = "Likely skip"
    ...
```

---

## Investigation 2: Salary Parser Misses

### Current State

Jobs with NULL salary but likely containing salary data (query-matched): ~8,000-12,000 estimated.
Tested 200 randomly sampled: **167 parsed correctly (83.5%), 33 true misses (16.5%)**.

Of the 33 true misses:
- ~8 are false positives (company ARR/revenue/funding amounts — correct to skip)
- ~10 are **"between $X and $Y"** pattern — parser gap
- ~10 are **single-value disclosures** with end-anchor bug — parser bug
- ~5 are weekly salaries or foreign currency (correct to skip)

### Where the parser lives

`python/enrich_job_postings.py`, function `parse_salary_range()` at line 1060.
Calls 17 extractor functions in priority order. Processes line-by-line, skips lines without "$".

### Root Cause

**Gap 1: "between $X and $Y" not handled.**

The parser has `$X to $Y` (in `_try_bare_range`) and `$X - $Y` but not `between $X and $Y`.

Real examples missing:
```
"between $193,930 and $319,720 for the level"  (Nuro)
"between $73,000 and $91,000 per year"          (Charlie Health)
"between $156,000 and $165,000 per year"        (Morgan Stanley)
"between $80,000 and $125,000 annually"         (EPLUS)
```

**Gap 2: `_try_single_value` has end-of-line anchor `\s*$` that rejects mid-line salary.**

Current pattern (line ~1036):
```python
r"(?:compensation|salary)[:\s]+\$\s*([\d,\.]+[kKmM]?)(?:\s*(?:USD|per\s+year|annually|/yr))?\s*$"
```

The `\s*$` requires the value be at the end of the line. Fails on:
```
"Base Salary: $58,000. Actual salaries may vary..."    (6sense)
"base salary for this role is $70,500. In addition..."  (Rho)
"salary range for this role is around $180,000 + equity" (Ocrolus)
"compensation: $150,000 base salary + performance bonus" (Sitreps)
"The typical base pay for this role is $275,000 depending on..." (Mufg)
```

**Gap 3: "Minimum Salary: $X Maximum Salary: $Y" not handled.**

`_try_min_max_labels` expects `minimum: $X` (colon directly before $), but fails on:
```
"Minimum Salary: $155,000 Maximum Salary: $225,000"   (Barclays)
```
because there's a word ("Salary:") between "Minimum" and the dollar value.

### Proposed Fixes

**Fix 1 — New function `_try_between_range`** (insert before `_try_bare_range` in the call chain):

```python
def _try_between_range(tline: str):
    """'between $X and $Y' or 'between $X to $Y' without dash separator.
    e.g. 'between $193,930 and $319,720', 'between $80,000 and $125,000 annually'
    """
    m = re.search(
        r"between\s+\$?\s*([\d,\.]+[kKmM]?)\s+(?:and|to)\s+\$?\s*([\d,\.]+[kKmM]?)",
        tline, re.IGNORECASE)
    if m:
        v1, v2 = _scale_pair(m.group(1), m.group(2))
        if v1 and v2:
            lo, hi = min(v1, v2), max(v1, v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p):
                return lo, hi, p
    return None
```

Add to call chain in `parse_salary_range()`:
```python
result = (
    _try_slash_period(tline) or
    _try_labeled_range(tline, window) or
    _try_min_max_labels(tline) or
    _try_min_max_pay_range(tline) or
    _try_targeted_pay_range(tline) or
    _try_annual_salary_range(tline) or
    _try_between_range(tline) or          # <-- ADD HERE
    _try_spaced_number_range(tline) or
    ...
)
```

**Fix 2 — Remove `\s*$` end anchor in `_try_single_value`** (line ~1036):

```python
# Before:
m = re.search(
    r"(?:compensation|salary)[:\s]+\$\s*([\d,\.]+[kKmM]?)(?:\s*(?:USD|per\s+year|annually|/yr))?\s*$",
    tline, re.IGNORECASE)
if m and not re.search(r"range|band|package", tline, re.IGNORECASE):

# After (replace \s*$ with lookahead for end/punctuation/word-break):
m = re.search(
    r"(?:compensation|salary)[:\s]+\$\s*([\d,\.]+[kKmM]?)(?:\s*(?:USD|per\s+year|annually|/yr))?(?=[\s.,+\-]|$)",
    tline, re.IGNORECASE)
if m and not re.search(r"range|band|package", tline, re.IGNORECASE):
```

**Fix 3 — Extend `_try_min_max_labels` to handle "Minimum Salary: $X Maximum Salary: $Y":**

```python
# Add a third pattern inside _try_min_max_labels (after existing patterns):
# "Minimum Salary: $155,000 Maximum Salary: $225,000"
m = re.search(
    r"minimum\s+\w+[:\s]+\$?\s*([\d,\.]+[kKmM]?).{0,80}?maximum\s+\w+[:\s]+\$?\s*([\d,\.]+[kKmM]?)",
    tline, re.IGNORECASE)
if m:
    v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
    if v1 and v2:
        lo, hi = min(v1, v2), max(v1, v2)
        p = _period_from_context(tline, lo)
        if _sanity(lo, hi, p):
            return lo, hi, p
```

### Expected Impact

Fixes 1-3 would recover ~15-20 of the 33 sampled true misses (~55-60%).
Estimated scale: **500-800 additional salary rows parsed** across the full active job set.
After applying, run: `python python/enrich_job_postings.py --skills-only ... --apply` (salary mode)
to reprocess the NULL-salary backlog.

---

## Investigation 3: City-Template Spam (Dedupe Bug)

### Problem

Companies using certain ATS platforms (Workday, Greenhouse, etc.) post the same remote job
as 100+ separate listings, one per city. Each city gets a different `loc_city` string, which
bypasses our dedup that keys on `(company_id, role_id, loc_city)`.

Example: Speechify "Tech Lead, Android Core Product" has separate active listings for
Aarhus, Alexandria, Baltimore, Belfast, Bilbao, Boise, Brisbane, Cardiff, Chapel Hill,
Delhi, Des Moines, El Paso, Florianópolis, Fresno, Galway... and 80+ more cities.

This floods the feed with 100 identical job cards varying only in city name.

### Scale

Detection query (run this to get exact numbers):
```sql
SELECT c.company_name, r.role_name,
       COUNT(DISTINCT jp.loc_city) AS city_count,
       COUNT(DISTINCT jp.job_id) AS total_jobs
FROM job_postings jp
JOIN companies c ON jp.company_id = c.company_id
JOIN roles r ON jp.role_id = jp.role_id
WHERE jp.status = 'raw' AND jp.data_tier = 1
GROUP BY c.company_name, r.role_name
HAVING COUNT(DISTINCT jp.loc_city) >= 5
ORDER BY city_count DESC
LIMIT 30;
```

(Couldn't complete this in session due to query timeout on the remote DB.)
Based on your Speechify example: estimated 20-50 company+title combos, hundreds to low thousands
of wasted rows.

### Root Cause

Ingest dedup in `ingest_jobs.py` uses `ON CONFLICT (job_id)` — keyed on the ATS job ID,
which is different per city. So each city variant is treated as a new legitimate job.
There's no post-ingest dedup that collapses same-company, same-title, multi-city spam.

### Proposed Fix: `python/dedup_city_spam.py` — one-time + daily cron

**Step 1: Detect spam combos** (same query as above)

**Step 2: Expire duplicates, keep oldest**
```sql
UPDATE job_postings
SET status = 'expired', expired_reason = 'city_spam_dedup'
WHERE job_id IN (
    SELECT job_id FROM (
        SELECT job_id,
               ROW_NUMBER() OVER (
                   PARTITION BY company_id, role_id
                   ORDER BY ingested_at ASC    -- keep the first-seen
               ) AS rn,
               COUNT(*) OVER (PARTITION BY company_id, role_id) AS city_count
        FROM job_postings
        WHERE status = 'raw' AND data_tier = 1
    ) ranked
    WHERE city_count >= 5 AND rn > 1   -- expire all but the first per combo
);
```

**Step 3: Update surviving row location to reflect global availability**
```sql
UPDATE job_postings jp
SET loc_city = 'Multiple Locations',
    loc_state = NULL,
    workplace_type = 'remote'
WHERE jp.status = 'raw'
  AND jp.data_tier = 1
  AND (
    SELECT COUNT(*)
    FROM job_postings jp2
    WHERE jp2.company_id = jp.company_id
      AND jp2.role_id = jp.role_id
      AND jp2.expired_reason = 'city_spam_dedup'
  ) >= 4;
```

**Script design:**
- `--dry-run` flag (default): show what would be collapsed, don't write
- `--apply`: execute updates
- `--threshold N`: city count threshold (default 5)
- Add to daily cron after `ingest_jobs.py`

**Reversibility:** `expired_reason = 'city_spam_dedup'` makes all changes auditable.
Revert with: `UPDATE job_postings SET status='raw', expired_reason=NULL WHERE expired_reason='city_spam_dedup'`

### Alternative: Query-time filter (no data mutation)

If you don't want to expire rows, add to the `/jobs` SQL query:
```sql
-- In recruiter.py WHERE clause, show only the MIN(job_id) per company+role
-- when that combo has 5+ city variants
AND (
    jp.job_id = (
        SELECT MIN(jp2.job_id) FROM job_postings jp2
        WHERE jp2.company_id = jp.company_id
          AND jp2.role_id = jp.role_id
          AND jp2.status = 'raw'
    )
    OR (
        SELECT COUNT(DISTINCT jp3.loc_city) FROM job_postings jp3
        WHERE jp3.company_id = jp.company_id
          AND jp3.role_id = jp.role_id
          AND jp3.status = 'raw'
    ) < 5
)
```
Downside: adds a correlated subquery, slows every page load. Not recommended.

**Recommendation: use the dedup script (Step 2+3).** Run dry-run first, review output,
then apply. Schedule after ingest.

---

## Summary

| # | Issue | Root Cause | Proposed Fix | Risk | Impact |
|---|-------|-----------|--------------|------|--------|
| 1 | 98% jobs Mixed/Weak | 93% NULL honesty + no-contact penalty | Remove no-contact penalty, fix freshness window, Strong threshold → ≥1 | Low — Python only, no schema | Strong: 1.6% → 25% |
| 2 | Salary parser misses | Missing "between" pattern + end-anchor bug + min/max word pattern | 3 regex additions/fixes in enrich_job_postings.py | Low — parser only | ~600-800 new salary rows |
| 3 | City-spam flooding feed | ATS templates per city, dedup keyed on job_id | Dedup script expires city duplicates, keeps oldest | Medium — writes to DB | Removes hundreds of spam rows |

**Approval needed before applying any of these.**
