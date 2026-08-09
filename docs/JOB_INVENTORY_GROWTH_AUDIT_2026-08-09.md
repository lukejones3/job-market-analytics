# Lander clean inventory growth audit: 55,870 to 100,000

**Audit date:** 2026-08-09

**Scope:** production database, frontend visibility boundary, ingestion/discovery code, Airflow orchestration, historical archive, live ATS endpoints, Common Crawl, Serper, employer career pages, sitemap enumeration, publication, canonicalization, and expiry

**North star:** at least **100,000 distinct, live, direct-employer, US-scope opportunities visible through the actual Lander frontend query**—not 100,000 raw rows and not a backend-only count.

## Executive verdict

The current Lander frontend really does contain **55,870 visible rows**. The earlier 63,167 figure is the backend publication snapshot, not the frontend inventory, and should not be used as the product count.

Those 55,870 visible rows represent **53,293 distinct canonical opportunities** across 2,943 companies. After collapsing duplicate source rows and fixing known foreign/stale leakage, the defensible starting inventory is likely about **49,000–51,000 clean canonical opportunities**. Reaching 100,000 therefore requires roughly **50,000 net-new clean opportunities**, not 37,000 and not 44,130 raw rows.

That goal is achievable, but the existing tenant-discovery loop cannot do it by itself. The high-ROI answer is an **employer-first Career Host Engine**:

1. Build a durable universe of employers from Lander's archive, historical jobs, Tier-2 lead data, SEC companies, and search-discovered employers.
2. Resolve each employer to its official domain and career host using known links plus Serper.
3. Fingerprint the career platform or public job API.
4. Enumerate all job detail pages through the platform API, recursive sitemap indexes, or a saved network-extraction recipe.
5. Accept only live, explicit-US, target-role, direct-employer jobs; quarantine everything else.
6. Deduplicate across ATS, career-site, and historical sources before the frontend count.

This is not theoretical. In a live test of 100 high-volume employers missing exact Tier-1 identity coverage, Serper returned a candidate career page for all 100; 99 pages responded; 54 had a recognizable career-platform fingerprint; and 68 exposed a sitemap. The resolved platforms included Workday, iCIMS, Oracle Cloud, Avature, UKG, Phenom, Radancy, Eightfold, Taleo, Paylocity, ADP, SuccessFactors, Greenhouse, and SmartRecruiters. Some results were aggregators or the wrong company, proving that search must feed a scored resolver and quarantine—not the publication table.

The central growth model reaches approximately **102,500 clean canonical jobs**. Its low case reaches only about 91,000, so this should be run as a measured coverage program with go/no-go yield gates, not as a promise that every source will hit its upper bound.

## 1. The count that matters

### 1.1 Production counts

Measured against production on 2026-08-09:

| Boundary | Jobs | Meaning |
|---|---:|---|
| All `job_postings` rows | 344,985 | Historical and current, all tiers and states |
| Raw active rows | 110,518 | Not equivalent to product-visible inventory |
| Backend `is_public = true` | 63,167 | Publisher boundary; broader than the frontend |
| Public canonical opportunities | 60,277 | Backend public after canonical collapse |
| **Actual frontend-visible rows** | **55,870** | Exact current Lander listing predicate |
| **Frontend-visible canonical opportunities** | **53,293** | Best current unique-opportunity baseline |
| Frontend-visible companies | 2,943 | Distinct company identities |

The frontend/backend gap is **7,297 rows**. It exists because `python/publish_snapshot.py` and `lander/lib/db/jobs.ts` apply different eligibility rules. The publisher admits rows that the frontend later removes with stricter location and company exclusions. SEO indexing is also driven from the broader publisher boundary, so Lander can ask Google to index job pages that its principal product query will not expose.

### 1.2 Exact frontend inventory by source

| Source | Visible rows | Share | Canonical opportunities | Companies |
|---|---:|---:|---:|---:|
| Greenhouse | 25,871 | 46.3% | 23,906 | 1,287 |
| Workday | 14,805 | 26.5% | 14,597 | 404 |
| Ashby | 6,894 | 12.3% | 6,788 | 542 |
| Lever | 4,296 | 7.7% | 4,244 | 427 |
| Amazon | 2,715 | 4.9% | 2,514 | 29 |
| Eightfold | 710 | 1.3% | 704 | 4 |
| Workable | 376 | 0.7% | 337 | 231 |
| SmartRecruiters | 175 | 0.3% | 175 | 20 |
| iCIMS | 28 | 0.1% | 28 | 6 |

The top two sources supply 72.8% of the current rows. This concentration is why another round of Greenhouse/Workday token guessing will have diminishing returns.

### 1.3 Inventory by product domain

| Domain | Visible jobs |
|---|---:|
| Engineering | 17,759 |
| Sales | 13,508 |
| Operations | 7,026 |
| Data / ML | 5,494 |
| Finance | 4,526 |
| Product | 3,703 |
| Marketing | 2,604 |
| Design | 1,250 |

Discovery code and naming still refer repeatedly to `data_ml_jobs`, even though the actual target product covers eight domains. Discovery validation, ranking, metrics, and query generation should use the shared role-scope taxonomy rather than the old data/ML shorthand.

## 2. What the present pipeline can and cannot yield

### 2.1 Source funnel

| Source | Raw active | Publication candidates | Removed before publication |
|---|---:|---:|---:|
| Greenhouse | 35,263 | 25,890 | 9,373 |
| Workday | 32,297 | 21,916 | 10,381 |
| Ashby | 10,173 | 6,894 | 3,279 |
| Lever | 6,570 | 4,298 | 2,272 |
| SmartRecruiters | 4,723 | 179 | 4,544 |
| Amazon | 3,095 | 2,720 | 375 |
| Eightfold | 1,270 | 801 | 469 |
| Workable | 458 | 381 | 77 |
| iCIMS | 98 | 88 | 10 |

Only about 3,269 otherwise clean/admitted/nonforeign rows are currently blocked solely on required enrichment fields, led by Workday (1,947), SmartRecruiters (502), Greenhouse (366), and Eightfold (217). Some will still fail the stricter frontend boundary. Clearing enrichment is necessary but cannot close a roughly 50,000-job canonical gap.

### 2.2 Registered and discovered tenant inventory

Current enabled/hiring registry counts include:

| Platform | Registered | Enabled | With active roles |
|---|---:|---:|---:|
| Greenhouse | 1,568 | 1,552 | 1,305 |
| Workday | 837 | 837 | 385 |
| Ashby | 620 | 620 | 523 |
| Lever | 547 | 537 | 436 |
| Jobvite | 175 | 175 | 175* |
| SmartRecruiters | 83 | 81 | 70 |
| iCIMS | 16 | 16 | — |
| Workable | 7 | 7 | — |
| Eightfold | 4 | 4 | — |

\* Jobvite validation currently overstates US/target yield, so this is an upper bound, not verified clean supply.

The generic candidate table had no unintegrated active candidates at audit time. Integrated tenants with zero current yield still carry optimistic validation estimates—approximately 4,489 Workday target roles, 2,383 Jobvite roles, 763 SmartRecruiters roles, and smaller Greenhouse/Lever totals—but inspection shows significant foreign, staffing, and validation pollution. The likely clean net gain from repairing and draining known candidates is **3,000–6,000**, not the sum of stored estimates.

### 2.3 Archive value

The archive is useful as a discovery graph and lifecycle history:

- 8,500 company records; 8,339 have appeared in job history.
- 3,667 companies have had Tier-1 jobs.
- 2,421 companies have historical target-role, US-scope jobs.
- 301 historically qualified companies currently have no public jobs.
- 118 dormant cohorts have at least five historical qualified rows.
- Historical tenants missing from the current registry include 95 Workday tenants and 264 Workable tenants.

The archive should not be republished. It contains closed roles, old identity errors, staffing firms, federal contractors excluded by the frontend, and past foreign leakage. It should seed current career-page resolution, then every role must be re-fetched and revalidated from the employer.

Tier-2 Adzuna data has similar value as a lead graph: 4,730 company identities and 17,293 nonexpired lead rows, with 4,293 exact company IDs not currently represented in Tier 1. Those records are noisy and sometimes duplicate the same employer under multiple names. Use the company name to find the official career page; never promote the aggregator row itself.

## 3. Quality and lifecycle audit

The 100,000 target must be a clean target. Today, several defects can increase the apparent count without increasing useful inventory.

### 3.1 Duplicate opportunities

- The frontend has 55,870 rows but only 53,293 canonical opportunities: **2,577 extra visible rows**.
- Across the broader public snapshot there are 1,707 multi-row canonical clusters and 2,890 excess rows.
- The largest cluster has 272 rows. `N2Publishingglassdoor` has 272 URLs with one canonical title/description combination.
- Launch2 has 93 public rows but 14 canonical opportunities; Hyphen Connect has 66/10; another remote cohort has 96/48.

`canonicalize_opportunities.py` groups rows, but the frontend query does not select a single representative per canonical opportunity. The count and results therefore still expose source/location clones.

**Required fix:** publish a representative opportunity view. Rank direct employer detail pages over mirrors, require one row per canonical opportunity, retain alternate URLs as provenance, and report both row count and canonical count during migration.

### 3.2 Foreign leakage

The current frontend admits `loc_country = 'unknown'` for Greenhouse, Lever, and Ashby. That was a pragmatic fallback, but it is no longer safe at this scale.

- Public unknown-country rows include 6,438 Greenhouse, 4,749 Workday, 635 Ashby, and 497 Lever.
- A conservative foreign-location regex finds at least 370 Greenhouse, 207 Ashby, 113 Lever, and 62 Workday public unknown-country rows.
- A title-only lower bound finds 1,219 public titles explicitly naming foreign geographies.
- Live examples included Armenia, Asia, Baku, Berlin, Brasil, Canada, Gdańsk, Herzliya, Latin America, London, Málaga, Toronto, and other non-US locations.
- Speechify alone had 1,424 public rows, 1,346 canonical opportunities, only 108 distinct descriptions, and 621 explicitly foreign titles while the location was `Distributed`.

Root causes include incomplete geographic vocabulary, no title-geography validation when location is vague, and substring logic in validation. `validate_ats_candidates._is_us_job()` treats `india` as a foreign substring, which can incorrectly match `Indiana` and `Indianapolis`.

**Required fix:** store structured location evidence rather than a single inferred label. Publication should require one of:

- a US `addressCountry` plus a valid US state/territory;
- a validated city/state pair;
- a remote role whose `applicantLocationRequirements` explicitly includes the United States; or
- a platform-native country field explicitly equal to the US.

Unknown remote roles should enter quarantine, not production. Title and description geography should be a contradiction check, not the primary location source.

### 3.3 Stale rows and tenant-run completeness

At audit time, 3,962 public rows had no `crawl_tenant`, including 1,678 Greenhouse, 955 Ashby, 751 Workday, 244 Lever, and smaller cohorts. Most of the Greenhouse/Ashby rows and 653 Workday rows were already older than three days.

`python/expire_jobs.py` only expires jobs whose `crawl_tenant` is non-null and whose tenant has a successful current `ingestion_tenant_runs` record. `python/ingest_jobs.py` creates tenant-run records by grouping accepted/upserted rows. A tenant returning zero jobs—or only jobs rejected before upsert—does not necessarily receive `complete_zero`. Consequently, closed roles can remain active indefinitely.

**Required fix:** the crawler must emit one tenant-run result for every attempted tenant independently of accepted jobs:

- `complete_nonzero`
- `complete_zero`
- `partial`
- `failed_retryable`
- `failed_terminal`

The attempted tenant set must be written before fetching. Expiry can then compare the complete observed set with existing rows. Backfill `crawl_tenant` from source URLs/metadata where deterministic; quarantine or revalidate unassignable orphans.

### 3.4 Company identity and intermediaries

The frontend uses an exact substring list for federal staffing and wrong-market companies. That cannot scale to thousands of new hosts. Search results and aggregator leads also create identity confusion: examples from the 100-company test resolved to Built In, Levels.fyi, a professional association board, a portfolio board, a similarly named bank, and the wrong corporate subsidiary.

**Required fix:** introduce a company identity/type gate:

- canonical employer domain and aliases;
- official career host ownership or a documented ATS relationship;
- `hiringOrganization.name` match to the canonical employer/alias;
- intermediary classification: direct employer, staffing/recruiting, aggregator, association board, government contractor, or unknown;
- no production activation for unknown ownership or intermediary type;
- domain/organization mismatch routed to manual review.

### 3.5 One publication boundary

Eligibility logic is copied across the SQL funnel, publisher, frontend, and SEO refresh. This has already diverged.

Create one database view or stable function such as `vw_lander_visible_opportunities` that owns:

- Tier/source policy;
- live lifecycle state;
- role scope;
- explicit-US evidence;
- company eligibility;
- minimum content quality;
- enrichment requirements;
- canonical representative selection.

The frontend listing, count, collections, analytics, SEO queue, sitemap, Company Radar, and publication safety gate should all consume it.

## 4. Discovery and ingestion defects to fix before scaling

### P0 correctness defects

1. **Publisher/frontend boundary mismatch:** `publish_snapshot.py` lacks the frontend's exact company and US-location boundary.
2. **Tenant-zero blind spot:** tenant completion is derived from accepted rows, so a genuinely empty board can leave old jobs alive.
3. **Null tenant orphans:** 3,962 public rows cannot participate in tenant-scoped expiry.
4. **Canonical groups are not selected:** the UI can count and show multiple rows for one opportunity.
5. **Unknown-country blanket allowance:** Greenhouse/Lever/Ashby unknowns include demonstrably foreign roles.
6. **Jobvite validation:** location validation effectively counts all titles as US in paths where details lack usable structured data.
7. **Validation substring bug:** `india` can collide with `Indiana`/`Indianapolis`.

### P1 observability and orchestration defects

1. `python/ats_discovery_health.py` queries nonexistent `job_postings.scraped_at`, so the scheduled report can fail.
2. That report joins generated registry IDs (`AT...`) to ingested company IDs (`C...`), making contribution statistics unreliable even after the timestamp fix. Yield must join by normalized source + `crawl_tenant`, then map canonical company identity.
3. `python/integrate_ats_candidates.py` writes `active_roles = us_jobs_count` rather than the validated target-role count.
4. Workday registry tokens include tenant/server/board, while `crawl_tenant` and refresh logic do not consistently use the same key; healthy boards can be shown as zero-yield.
5. Jobvite, BambooHR, and Taleo are run with `--accept-empty`. That is acceptable only when the configured enabled-tenant count is zero. Once discovery activates tenants, a zero result must fail or explicitly record every board as `complete_zero`.
6. Discovery validation is ranked around the old data/ML field even though eight domains are in scope.

### P2 coverage defects

1. `coverage_ingest.py` is a one-off tool, not an Airflow pipeline. It records no discovery evidence, tenant runs, retries, expiry state, or per-host quality metrics.
2. Sitemap parsing reads only one XML level; it does not recurse sitemap indexes, process gzip, honor `lastmod`, or use conditional requests.
3. JSON-LD crawling is sequential and has no host queue/rate policy.
4. The company probe guesses `companyname.com`, scans shallow fixed paths, and reads only an initial HTML slice. It misses redirects, JavaScript career sites, custom hosts, and network APIs.
5. Serper discovery uses a small fixed result window, lacks durable pagination/caching, and has inherited data/ML-heavy queries.
6. Common Crawl tenant mining is generalized mainly for Workday and uses a capped/offset CDX approach. Bulk discovery should use the URL Index Parquet dataset, partitioned by platform pattern.
7. `discover_company_ats.py` can flag SuccessFactors and Breezy but cannot extract usable tokens for them. SuccessFactors is then explicitly classed as discoverable but not harvested.
8. Major career families are absent or effectively absent: Oracle Recruiting Cloud, Avature, Phenom, Radancy, UKG, Dayforce, ADP, Paylocity, PageUp, SuccessFactors, Recruitee, Personio, Comeet, and custom JobPosting sites.

## 5. External research and live experiments

All projections below are discovery evidence, not guaranteed production yield. Samples were fetched live on 2026-08-09 and conservatively classified where possible.

### 5.1 Common Crawl tenant universe

The July 2026 Common Crawl URL index contained approximately:

| Platform pattern | Unique tenant-shaped hosts/paths |
|---|---:|
| BambooHR | 3,407 |
| Workable | 3,020 |
| Ashby | 2,117 |
| Greenhouse job-board paths | 889 |
| Personio | 878 |
| Recruitee | 613 |
| iCIMS | 544 |
| Oracle Candidate Experience | 157 hosts / 185 host-site pairs |
| Jobvite | 70 |

These are seeds, not verified active US employers. One crawl can miss robots-blocked or newly launched hosts, and historical URLs can be dead. Production discovery should union several recent indexes and live-validate each tenant.

Common Crawl's official guidance points bulk users to its columnar URL Index/Athena data rather than overloading the CDX server. The currently published July 2026 crawl is `CC-MAIN-2026-30` ([Common Crawl URL Index](https://commoncrawl.org/url-index), [index collections](https://index.commoncrawl.org/)).

### 5.2 Public-platform samples

#### BambooHR

A seeded random sample of 400 of the 3,407 candidate tenants produced:

- 365 HTTP 200 responses;
- 306 nonempty boards;
- 2,681 total openings;
- 655 target-role openings;
- 268 explicitly US target openings, 365 foreign, and 22 remote/unknown after detail checks.

A naive expansion implies about 2,283 explicit-US target roles in the indexed universe. Because Lander's Bamboo registry is essentially empty, a quality-gated full miner plausibly contributes **1,800–2,300 net clean roles**.

#### iCIMS

A random sample of 250 of 544 candidate hosts produced:

- 248 HTTP 200 responses;
- 210 nonempty boards;
- 4,656 first-page openings;
- 861 target-role detail pages;
- 761 explicit-US target roles and 100 foreign roles;
- `directApply = true` on all 861 sampled JobPosting objects.

The first-page-only naive expansion is about 1,656 explicit-US target roles; correct pagination should increase the ceiling. Lander currently has only 28 visible iCIMS rows from six companies. A plausible net gain is **1,500–2,500**.

#### Oracle Recruiting Cloud

The public Candidate Experience sites exposed a live requisition collection at the career-site origin. Across 185 discovered host/site pairs:

- 184 responded to the public job request and one returned 403;
- sites reported 44,395 global openings in aggregate;
- first pages contained 13,444 rows / 12,174 unique host-requisition pairs;
- 2,473 were target-role titles;
- 431 were conservatively explicit-US target roles across 27 hosts.

The Common Crawl seed was globally biased and incomplete, so this is a floor. Oracle is strategically valuable because it covers large employers and its public candidate sites can be enumerated without publishing aggregator content. The collection documented by Oracle is `recruitingCEJobRequisitions`; Oracle labels these REST operations for its candidate-experience implementation, so production must contract-test each public site and retain a JobPosting/HTML fallback rather than assuming a permanently supported public API ([Oracle Recruiting CE requisitions](https://docs.oracle.com/en/cloud/saas/human-resources/farws/api-recruiting-ce-job-requisitions.html)).

#### Personio and Recruitee

Personio's sample was reachable but Europe-heavy: 400 candidates yielded 276 live XML feeds, 2,226 openings, 734 target-role titles, and only 14 conservatively explicit-US target roles. Recruitee's unauthenticated Careers API sample was rate-limited and similarly had low US yield among successful responses. Both are cheap to support but lower priority for the US growth target.

Their public interfaces are nevertheless straightforward: Personio exposes a tenant XML feed and recommends regular synchronization ([Personio XML integration](https://support.personio.de/hc/en-us/articles/207576365-Integrate-jobs-from-Personio-into-your-company-website-via-XML)); Recruitee exposes an unauthenticated Careers Site API with an offers collection ([Recruitee Careers Site API](https://docs.recruitee.com/reference/intro-to-careers-site-api)).

### 5.3 Serper role/state discovery test

Forty paginated, partitioned queries combined an exact target title, a US state, recency, and career/job URL patterns while excluding major aggregators. Results:

- 40 successful queries;
- 44 unique organic candidates on 41 hosts;
- 36 live HTTP pages;
- 20 pages with JobPosting JSON-LD;
- 19 explicit-US target JobPosting objects;
- 17 on custom or currently unrecognized hosts.

Legitimate direct-employer discoveries included career pages for Capital One, Empower, Baxter, Ulta, Varsity Brands, Constellation, HP, Pentair, Adobe, Keysight, Disney, and Intuit. The result page itself is not inventory; its value is revealing a host that can be fully enumerated.

Serper provides real-time Google results, location/language controls, and charges for successful searches. Query outcomes therefore need to be cached and measured as hosts-per-query, not jobs-per-query ([Serper](https://serper.dev/)).

### 5.4 Sitemap amplification test

Eight discovered career hosts exposed 14,136 job-shaped URLs through sitemaps:

| Employer | Sitemap job URLs | Live sample result | Current overlap |
|---|---:|---|---|
| Capital One | 1,653 | 64/100 fresh US target | Mostly already in Workday |
| Ulta | 10,015 | 1/100 fresh US target | Absent from current coverage |
| Varsity Brands | 133 | 51/100 fresh US target | Absent |
| Constellation | 220 | 38/100 fresh US target | Absent |
| HP | 702 | 30/100 fresh US target | Partial Workday overlap |
| Pentair | 92 | 22/92 fresh US target | Absent |
| Adobe | 747 | 59/100 fresh US target | Mostly Workday overlap |
| Keysight | 574 | 21/100 fresh US target | Absent |

Naive within-host expansion suggests roughly 395 target roles from the five clearly absent employers alone. The exact projections are selection-biased, but two conclusions are strong:

1. One good search result can unlock a whole employer, not one job.
2. Cross-source canonicalization is mandatory because custom career pages can mirror already-covered Workday requisitions.

The sitemap protocol explicitly supports sitemap indexes, gzip, and `lastmod`; the current one-level parser leaves this amplification unused ([Sitemaps protocol](https://www.sitemaps.org/protocol.html), [Google sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)).

### 5.5 Employer-first resolution test

The top 100 Tier-2 company leads without exact Tier-1 company identity were resolved with one Serper query per company:

| Result | Count |
|---|---:|
| Search calls succeeded | 100 |
| Candidate page found | 100 |
| Candidate page returned HTTP 200 | 99 |
| Candidate host exposed a sitemap | 68 |
| Candidate page had a known career-platform fingerprint | 54 |
| Custom/unfingerprinted | 46 |

Fingerprint occurrences included 18 Workday, seven Avature, seven Oracle Cloud, seven UKG, six iCIMS, six Phenom, five Radancy, three Eightfold, and smaller Taleo, Paylocity, ADP, SuccessFactors, Greenhouse, and SmartRecruiters cohorts. Some pages had multiple fingerprints.

The test also exposed the resolver's failure modes: similarly named organizations, subsidiaries, aggregator company pages, recruiting firms, and search-result job pages. Search resolution needs an explicit confidence model:

- exact/alias employer-name agreement;
- official corporate-domain agreement;
- career link reachable from the official corporate domain, or recognized ATS tenant relationship;
- hiring organization agreement on sampled leaf pages;
- aggregator/intermediary rejection;
- confidence threshold plus manual review for high-yield ambiguous hosts.

### 5.6 Official ATS interfaces already support high-quality extraction

For existing platforms, Lander should continue to use public employer-owned posting interfaces rather than search snippets:

- [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html): public board GET endpoints without authentication.
- [Lever Postings API](https://github.com/lever/postings-api): published postings with workplace/location information.
- [Ashby Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api): published/listed state, location/country, compensation, and job metadata.
- [SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/posting-api): public posting retrieval.
- [Comeet Careers API](https://developers.comeet.com/reference/careers-api-overview): career-site job exposure, subject to its published throttling.

Where a vendor API requires customer authentication (for example, enterprise HR APIs), use the employer's public candidate pages, sitemaps, and JobPosting data instead of seeking private tenant credentials.

Google's JobPosting specification gives the core acceptance semantics for custom sites: structured data belongs on individual job detail pages; `hiringOrganization` must be the actual employer; remote roles should use `applicantLocationRequirements`; and expired/filled pages must be removed or marked with `validThrough` ([Google JobPosting structured data](https://developers.google.com/search/docs/appearance/structured-data/job-posting)).

## 6. The system to build: Career Host Engine

### 6.1 Durable registries

Create `career_hosts` with at least:

- canonical company ID, employer name, aliases, official domain;
- careers URL and job host;
- platform family, tenant token, public API or extraction recipe;
- discovery source and evidence URL;
- resolver confidence and identity-review state;
- country/market evidence;
- lifecycle status, last success, last nonempty run, failure streak;
- ETag, Last-Modified, sitemap `lastmod`, next crawl time;
- last total/target/US/accepted/duplicate/rejected counts.

Create `career_host_runs` independently of accepted jobs:

- requested/fetched/detail page counts;
- extractor version;
- complete/partial/failed state;
- target-role and explicit-US counts;
- per-reason rejection counts;
- canonical net-new count;
- anomaly metrics and activation decision.

Create `career_host_candidates` for unresolved/quarantined evidence. Search and Common Crawl discoveries belong here until identity and live-yield validation pass.

### 6.2 Seed universe

Seed in this order:

1. Current registry and every historical Tier-1 tenant.
2. The 8,500-company Lander archive, prioritizing 301 dormant qualified companies and missing historical tenants.
3. Tier-2 company leads, ranked by recent target-role count but deduplicated by employer identity.
4. SEC ticker/company metadata as a clean public-company universe ([SEC company tickers](https://www.sec.gov/file/company-tickers), [SEC filing APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)).
5. Common Crawl platform-host miners across several recent crawl indexes.
6. Serper long-tail results from role × state × recency partitions, excluding known hosts and aggregators.

### 6.3 Resolve once, crawl many

For each employer:

1. Reuse a known official domain or resolve it from evidence.
2. Follow corporate-site careers navigation and redirects.
3. If unresolved, issue a narrow Serper query for the official career/jobs page.
4. Score domain/name/platform agreement; reject known aggregators.
5. Fetch `robots.txt` and collect all sitemap declarations.
6. Recursively process sitemap indexes and `.gz` files; prioritize changed job-shaped URLs.
7. Detect known ATS hosts/links and switch to the public platform adapter.
8. Parse JobPosting JSON-LD on leaf pages.
9. For JavaScript sites without server-rendered JobPosting data, run a browser/network capture once to learn the public jobs endpoint and store a reusable extraction recipe. Do not browser-render every job nightly.
10. Sample details, validate identity/US/target/lifecycle quality, then activate or quarantine the host.

### 6.4 Search budget strategy

Serper tokens should be spent on **host discovery**, not individual-job harvesting.

- Resolve only companies whose domain/career host is unknown or stale.
- Cache successful employer resolution for 90 days and failures with exponential retry.
- Run one company-resolution query before trying variants.
- Partition long-tail discovery by target title family, state, recency, and result page.
- Exclude all known ATS hosts, registered career hosts, aggregators, and staffing domains at query time.
- Stop a query family when net-new verified hosts per 100 calls falls below a set threshold.
- Record query, result rank, destination, resolution score, activated host, and seven-day surviving canonical jobs.

A reasonable first controlled tranche is 5,000 unresolved employer queries plus roughly 1,200 role/state/page discovery queries. The live tests show this is enough to measure the true host and job yield before spending the rest of the token pool.

### 6.5 Platform priority

**Immediate, evidence-backed:**

1. iCIMS full pagination and tenant miner.
2. BambooHR tenant miner and strict US detail validation.
3. Oracle Candidate Experience site discovery/adapter.
4. Repair current Jobvite validation before trusting its 175 integrated tenants.
5. Recursive sitemap + JobPosting crawler as a scheduled, observable source.

**Next enterprise families:**

1. Avature.
2. Phenom and Radancy career sites.
3. UKG public career pages.
4. SuccessFactors Career Site Builder/job-delivery pages.
5. PageUp and Paylocity.
6. Eightfold expansion beyond the four current companies.

SAP documents both standardized Career Site Builder sites and active-job delivery feeds, making SuccessFactors a resolvable family even when a universal anonymous API is not available ([SAP career sites](https://help.sap.com/docs/successfactors-recruiting/setting-up-and-maintaining-sap-successfactors-recruiting/career-sites-for-sap-successfactors-recruiting), [SAP job delivery/feed management](https://help.sap.com/docs/successfactors-recruiting/setting-up-and-maintaining-sap-successfactors-recruiting/job-delivery-and-site-feed-management)).

**Cheap but lower US yield:** Personio, Recruitee, Comeet.

**Do not make private authenticated APIs the dependency:** ADP and Dayforce expose enterprise APIs, but access is customer/application specific. Discover their public employer career pages and extract public postings instead.

## 7. Clean 100,000 growth model

The lanes below are modeled as **net-new canonical opportunities after cross-source dedup**, so they are not simply summed from platform-reported raw totals.

| Stage | Low | Central | High | Evidence/gate |
|---|---:|---:|---:|---|
| Clean canonical baseline after current cleanup | 49,000 | 50,000 | 51,000 | One visibility view; foreign/stale cleanup; representative canonical rows |
| Existing registry, enrichment, archive-tenant repair | +4,000 | +5,500 | +7,000 | Seven-day surviving jobs from already known tenants only |
| Common Crawl tenant mining + iCIMS/Bamboo/Oracle/current ATS expansion | +5,000 | +7,000 | +9,000 | Live-validation samples above; exclude known employer/tenant identities |
| Employer-universe resolver + career-host crawler | +20,000 | +24,000 | +28,000 | Archive/Adzuna/SEC employers with verified official hosts |
| Long-tail role/state Serper host discovery + custom sites | +10,000 | +12,000 | +15,000 | Net-new hosts only; sitemap/API amplification; seven-day survival |
| Direct employer feeds + remaining enterprise families | +3,000 | +4,000 | +6,000 | Authenticated employer identity; source lifecycle contract |
| **Result** | **91,000** | **102,500** | **116,000** | Clean, live, canonical frontend opportunities |

The central case crosses 100,000. The low case does not. Therefore:

- Build toward **105,000–110,000 clean canonical opportunities** so ordinary daily churn does not drop the frontend below 100,000.
- Do not claim success from raw or backend public rows.
- Reforecast after each lane using actual seven-day surviving net-new inventory.
- If employer-resolution yield underperforms, expand the verified employer universe and enterprise platform adapters before relaxing quality gates.

## 8. Acceptance and quarantine gates

Every new source/host should shadow-ingest before activation.

### Per job

- HTTP 200 live detail page or successful public platform detail response.
- Target role according to the shared eight-domain taxonomy.
- Actual employer identity matches the canonical company.
- Direct apply or employer-owned application path.
- Explicit US location evidence; remote eligibility explicitly includes US.
- Nonempty meaningful description, stable source requisition ID, and usable canonical URL.
- `validThrough` not past; no closed/filled signal.
- Not a staffing, aggregator, association, or scraped-board posting unless product policy explicitly allows that class.
- Canonicalized against every existing source before counting.

### Per host/tenant

- At least 100 sampled details or all jobs for small boards before broad activation.
- Duplicate title/description ratio below a defined threshold.
- Hiring-organization mismatch rate below 1%.
- Foreign leakage below 1% on audited samples.
- No all-same-description/template spam cohort.
- Job count change within an anomaly envelope or manually approved.
- Complete crawl state recorded even when zero jobs are accepted.
- Seven-day live survival measured before the source contributes to growth forecasts.

### Primary metrics

1. Clean frontend canonical count and seven-day low-watermark.
2. Net-new seven-day surviving canonical jobs by source and discovery lane.
3. Verified hiring hosts per 100 Serper calls.
4. Accepted canonical jobs per activated host.
5. Sitemap/API amplification per discovered host.
6. Explicit-US evidence rate.
7. Cross-source duplicate rate.
8. Foreign, intermediary, identity-mismatch, and stale/dead rejection rates.
9. Tenant completion coverage: attempted tenants with a terminal run record.
10. Count parity between publisher, frontend, SEO, and analytics.

## 9. Recommended delivery sequence

### Phase 0: make the count truthful

1. Create the single visible-opportunity database boundary.
2. Select one canonical representative.
3. Fix tenant-run completeness and null-tenant expiry.
4. Add explicit structured US evidence and title/location contradiction checks.
5. Fix discovery health SQL/joins, Workday tenant keys, target-role metrics, and Jobvite validation.

Expected effect: the count may decrease. That is a correction, not lost coverage.

### Phase 1: drain known clean supply

1. Clear the enrichment-only backlog.
2. Revalidate zero-yield integrated boards.
3. Restore historical tenants missing from the registry.
4. Remove `--accept-empty` for sources with enabled tenants unless every tenant reports an explicit zero.

Gate: at least 4,000 net-new seven-day surviving canonical jobs.

### Phase 2: enumerate public ATS tenants broadly

1. Generalize Common Crawl URL-index miners by platform.
2. Finish iCIMS pagination and discovery.
3. Populate BambooHR and Oracle registries.
4. Add scheduled, observable adapters with tenant-complete lifecycle state.

Gate: at least 5,000 additional net-new clean jobs and less than 1% audited foreign/identity leakage.

### Phase 3: ship Career Host Engine

1. Add the career host/candidate/run registries.
2. Resolve archive and Tier-2 lead companies through known domains and Serper.
3. Implement recursive sitemap/gzip/conditional fetching.
4. Add JobPosting detail validation.
5. Add one-time JavaScript network fingerprinting and reusable extraction recipes.
6. Activate hosts in small quarantined cohorts.

Gate: at least 20,000 net-new seven-day surviving canonical jobs from verified employers.

### Phase 4: long tail and enterprise families

1. Run role × state × recency host discovery.
2. Add Avature, Oracle, Phenom/Radancy, UKG, SuccessFactors, PageUp, and Paylocity families based on observed unresolved-host share.
3. Offer a direct employer feed specification and self-verifying domain handshake.
4. Continue until the seven-day frontend low-watermark exceeds 105,000.

## 10. What not to do

- Do not count `is_public` as frontend inventory.
- Do not publish Adzuna, Serper, Google, Common Crawl, or archive rows directly.
- Do not loosen role, employer, location, content, or lifecycle gates to reach a headline number.
- Do not add thousands of guessed tenant slugs without current validation and tenant-run state.
- Do not browser-render every job page nightly; capture public network recipes once and crawl the underlying endpoint responsibly.
- Do not sum platform estimates before employer and canonical deduplication.
- Do not let a source-wide successful crawl protect unattempted or failed tenants from expiry.
- Do not treat a search result titled “Company jobs” as proof that the page belongs to that company.

## 11. Bottom line

Lander's current 55,870 frontend rows are real, but the clean unique base is closer to 50,000 after known duplicate, foreign, and stale corrections. The current discovery system can probably supply another 4,000–7,000 clean jobs; it cannot produce the whole second half of the target.

The path to 100,000 is to stop thinking of discovery as “find another ATS token” and start treating the web as an employer-to-career-host graph. Lander already has the seeds: 8,500 archived companies, thousands of Tier-2 employer leads, historical tenants, Serper access, and direct source adapters. Common Crawl cheaply identifies platform-shaped hosts; Serper resolves official career pages; sitemaps and public APIs amplify one discovery into a whole company; structured evidence and quarantine prevent garbage from reaching the product.

Build the truth/lifecycle layer first, then the Career Host Engine. The measured central case reaches roughly 102,500 clean canonical jobs, and a 105,000–110,000 operating buffer makes a durable 100,000-job frontend attainable without padding the count.

## Appendix A: methodology and limitations

- Production counts are point-in-time measurements from 2026-08-09 and will move with nightly ingestion/publication.
- Frontend counts reproduce the predicates in `lander/lib/db/jobs.ts` and company exclusions in `lander/lib/filters.ts` rather than using `is_public` alone.
- Canonical counts use the current canonical opportunity assignment; the audit separately identifies defects in how representatives are exposed.
- “Foreign-title” and “foreign-location” figures are conservative lower bounds from explicit geography patterns, not exhaustive classifiers.
- Common Crawl counts are unique host/path seeds from the July 2026 URL index, not verified tenants or market-share estimates.
- BambooHR, Personio, Recruitee, and iCIMS sampling used a fixed random seed and live responses. Projections are simple expansions and are labeled as such.
- Oracle counts are based on discovered public Candidate Experience site pairs and first-page target classification; global site discovery and pagination remain incomplete.
- Serper tests measured discovery yield only. Search rankings are dynamic, and candidate pages require employer-identity verification.
- Sitemap within-host projections are sample estimates and can overstate jobs where historical URLs remain indexed. Production accepts only current live leaf pages.
- Growth ranges are net-new planning estimates. Each phase must be reforecast from canonical, seven-day surviving production yield before the next spend tranche.
