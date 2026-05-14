# Phase 1: Salary Parsing Root Cause Analysis

**Date**: 2026-05-14
**Scope**: Jobs on landerjob.com with obvious salary info in description but NULL salary fields
**Status**: Read-only investigation complete. Awaiting approval to proceed to Phase 2.

---

## Finding 0 (Critical, Flagged Separately): Anthropic API Credits Exhausted

The production enrich log from May 13 contains **4,732 API errors** all reading:

> `"Your credit balance is too low to access the Anthropic API."`

Both `classify_role` and `extract_salary_llm` have been silently failing since at least May 13. This is independent of the regex problem and needs an immediate credit top-up before any LLM-related fixes make sense.

---

## Scope

| Metric | Count |
|---|---|
| Total jobs (status=raw) | 70,146 |
| Jobs with salary populated | 37,733 (54%) |
| Jobs: `$`+digit + salary keyword + NULL salary | **7,614** |

All 7,614 contain at least one `$` followed by a digit somewhere in the description. This is the full universe needing investigation.

---

## Stage Failure Buckets

| Stage | Count | Example job_ids |
|---|---|---|
| **Never enriched — cron gap + queue starvation** | ~7,553 | J6e68c8c6fd, Jc03aa6c30b, J4a6fa13ebd, Ja6902c0202 |
| **Genuine "no salary disclosed" — content gap** | ~59 | J7cdb23b75c (Deel), J059c1dd547 (Writer), Je49e4f9d4e (Synthesia) |
| **LLM not invoked — API credits depleted** | 94 (last 5 runs) | any job from past 5 enrich runs |
| **LLM attempted but failed (API errors)** | ~75 (~80% failure rate) | (from log: 39/49 + 15/18 + 12/14 + 13/13 = ~79 failures) |
| **Written then lost** | 0 | — |
| **Regex gap (specific edge cases)** | ~6 | J600bc49507 (MFS), Jd873d17c2d (RJ), Jd4e14940a9 (Comcast x3) |

---

## Root Cause #1 (Dominant): `enrich_job_postings.py` Changed from Daily to Weekly

**This is the main cause of ~99% of the missing salary records.**

The live production crontab has:

```
# Full LLM enrichment — NULL salary + NULL classification only (weekly Sunday 03:00 UTC)
0 3 * * 0 cd /opt/job-market-analytics && ENRICH_MAX_LLM_CALLS=10000 \
  python python/enrich_job_postings.py --apply --only-missing >> logs/enrich_llm.log 2>&1
```

The daily `30 6 * * *` enrich job is **gone**. The last daily run was May 13. Since then
(1.5 days), 1,721 new jobs with parseable salary info have accumulated. By next Sunday
(May 18) that number will be ~5,000+.

**The `crontab.txt` in the repo is stale** — it still shows `30 6 * * *` but that is not
what is running.

**Throughput before the change was also too slow**: the May 9–13 runs used `--only-missing`
with the default **limit of 200** (the `main()` default). At 200 jobs/day and thousands of
jobs ingested daily, the queue was never draining. The `ORDER BY ingested_at DESC` means
new jobs always preempt older ones, so a job that is only missing salary but has everything
else enriched gets permanently deprioritized.

**Verification**: `parse_salary_range(desc, skip_llm=True)` was run against 30 randomly-sampled
May-14 failing jobs. **All 30 returned a valid salary.** The regex works — these jobs have
simply never been run through enrichment.

---

## Root Cause #2 (Structural): `--only-missing` Query Conflates All Missing Fields

The `--only-missing` query selects jobs missing **any** of: `company_id`, `role_id`,
`location_id`, `workplace_type`, `employment_type`, `experience_level`, `salary_min`,
`salary_max`, `salary_period`, `role_category`, or skills. Every freshly-ingested job
needs all of these. So the 200-job daily limit is consumed entirely by fresh ingests,
leaving jobs that are *only* missing salary to perpetually wait.

There is no dedicated "salary rescan" pass in the current daily schedule. The
`--rescan-salary` mode existed on May 4–8 (ran 3×/day with higher limits) but was removed
when the schedule was restructured on May 9.

---

## Root Cause #3 (Minor): Regex Gaps for ~6 Specific Jobs

Testing all 61 jobs older than 7 days (the ones that went through enrich multiple times
but still have NULL salary):

- **59/61 are genuine content gaps**: companies wrote "competitive salary" /
  "competitive compensation" with no dollar figure. Regex correctly returns null.
  LLM would also return null — there is nothing to extract.
- **~6 have parseable content but regex misses them**:

  1. **`_try_single_value` guard too broad** (MFS, J600bc49507):
     `"Base Salary: $ 115,000 ... for that reason, we're including the salary range for
     this position..."` — the guard `not re.search(r"range|band|package", tline)` fires
     because "salary range" appears ~150 chars later in the same paragraph. The match is
     correct but the guard rejects it.

  2. **"Base Pay:" not in single-value labels** (Comcast x3, Jd4e14940a9 etc.):
     `"Compensation Base Pay: $32.00"` — `_try_single_value` only checks
     `(?:compensation|salary)` as the preceding keyword. "Base Pay:" does not match.
     The labeled-range patterns require two numbers, which this does not have.

  3. **`SALARY : $137,000 per year` blocked by late-appearing "range"** (Raymond James,
     Jd873d17c2d): "SALARY : " should match `_try_single_value` but
     "The total compensation ... salary range..." appears later in the same line,
     triggering the `range` guard and rejecting the otherwise-valid single value.

No `&#xa;` normalization issue was confirmed as causing actual failures. Workday
descriptions with `&#xa;` in the sample were all in the genuine "no dollar figure"
category.

---

## Cron Audit — Last 14 Days

| Date | Ran? | Limit | Log file | LLM status |
|---|---|---|---|---|
| May 4 | Yes (09:00 UTC) | 5,000 | enrich.log | OK |
| May 4 | Yes (10:00 UTC) | 500 | enrich_salary.log | OK |
| May 4 | Yes (11:00 UTC) | 2,000 | enrich2.log | OK |
| May 5–8 | Yes (3×/day) | same as above | various | OK |
| May 9 | Yes (06:30 UTC) | **200** (default) | enrich.log | OK |
| May 10 | Yes (06:30 UTC) | 200 | enrich.log | OK |
| May 11 | Yes (06:30 UTC) | 200 | enrich.log | OK |
| May 12 | Yes (06:30 UTC) | 200 | enrich.log | OK |
| May 13 | Yes (06:30 UTC) | 200 | enrich.log | **API dead (4,732 errors)** |
| May 14 | **No enrich ran** | — | — | API still dead |

No OOM kills, timeouts, or non-zero exits observed. All runs terminated cleanly with
`✅ Applied (COMMIT)`. The failure mode is silent: the script commits what regex found,
the LLM portion silently returns null (HTTP 400), and salary stays blank.

**No `llm_enrichment_log` table exists** — there is no queryable audit trail of which jobs
had LLM attempted vs skipped vs succeeded.

---

## LLM Salary Success Rate (Before API Died)

From the last 4 enrich runs (May 13):

| Run | Salary LLM queued | Salary extracted | Hit rate |
|---|---|---|---|
| Run 1 | 49 | 10 | 20% |
| Run 2 | 18 | 3 | 17% |
| Run 3 | 14 | 2 | 14% |
| Run 4 | 13 | 0 | 0% (API dead by this point) |

The declining hit rate across runs likely reflects that earlier runs processed jobs with
more extractable salary content, and later runs hit the "competitive salary" jobs where
even LLM returns null. The final run returning 0/13 coincides with API credit exhaustion.

---

## Dominant Root Cause Summary

The salary miss rate on landerjob.com is caused almost entirely by:

1. **Enrich no longer runs daily** — weekly is not enough for daily ingest volume
2. **When it did run daily, throughput was too low** — 200-job limit, all consumed by
   fresh ingests that need every field, leaving salary-only-missing jobs to wait indefinitely
3. **LLM fallback is currently dead** — Anthropic API credits exhausted

The regex itself is correct for all standard formats (`&mdash;` ranges, `$Xk–$Yk`,
multi-line blocks, space after `$`, `$X to $Y`, etc.). There are minor edge-case gaps
(~6 jobs) worth fixing but they are not the dominant cause.

---

## Open Questions for Phase 2 Approval

1. Was there a reason `enrich_job_postings.py` was intentionally moved to weekly?
   Restoring daily at 06:30 is the obvious fix but want to confirm there wasn't a
   deliberate cost/performance decision behind the change.

2. Confirm: backfill should run **regex-only first**, report numbers, then await LLM
   approval (as specified in the task brief). LLM is also blocked until API credits are
   restored.

3. The `--rescan-salary` mode (which filters specifically for `salary_max IS NULL AND
   description LIKE '%$%' AND salary keywords`) is the right tool for the backfill — it
   avoids touching jobs that already have salary and won't re-queue jobs based on other
   missing fields. Confirm this is the approach wanted.
