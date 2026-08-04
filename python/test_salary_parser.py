#!/usr/bin/env python3
"""
Test harness for parse_salary_range().
Run: python3 python/test_salary_parser.py
"""
import sys
sys.path.insert(0, 'python')
from enrich_job_postings import parse_salary_range
from decimal import Decimal

TESTS = [
    # (name, text, expected_min, expected_max, expected_period)
    # --- Already working ---
    ("standard range", "Salary range $81,600 - $102,000 per year", 81600, 102000, "year"),
    ("OTE", "OTE (On-Target Earnings): $145k-$150k", 145000, 150000, "year"),
    ("tilde", "Pay range: $190,000 ~ $225,000", 190000, 225000, "year"),
    ("multi-line", "Compensation\nFor Full-Time\nUS based: $180,000/year to $260,000/year", 180000, 260000, "year"),
    ("slash yr", "$81.6K/yr - $102K/yr", 81600, 102000, "year"),
    ("Quizlet", "starting base salary of $207,856 - $272,810", 207856, 272810, "year"),
    ("Akuna", "minimum base salary starts at $145,000", 145000, 145000, "year"),
    ("Circa hourly", "Hourly rate: $35 – $53 hour.", 35, 53, "hour"),
    ("MKS2 pay rate", "Pay Rate: $41.82 - $45.67 per hour", 41.82, 45.67, "hour"),
    ("KoBold USD", "US base salary range for this full-time exempt position is $140,000 - $240,000 (USD)", 140000, 240000, "year"),
    ("CAD range", "base compensation range for this position is CAD $78,000 - CAD $115,000", 78000, 115000, "year"),
    ("Alt single", "Compensation: $200,000", 200000, 200000, "year"),
    ("Harmonic", "$60k-$75k salary + variable pay", 60000, 75000, "year"),
    ("HHMI min/max", "AI Engineer I: $96,325.60 (minimum) - $120,407.00 (midpoint) - $156,529.10 (maximum)", 96325.60, 156529.10, "year"),
    ("Guidde OTE", "salary range of $120,000 OTE - $160,000 OTE", 120000, 160000, "year"),
    ("Georgetown", "the full range of anticipated compensation is: $80,429.00 - $157,238.93", 80429, 157238.93, "year"),
    ("Global pay range", "Pay Range: $73,600.00 - $110,400.00", 73600, 110400, "year"),
    ("Rochester hourly", "Compensation Range: $21.36 - $29.90", 21.36, 29.90, "hour"),
    ("Align plus", "salaried position offering $250,000+", 250000, 250000, "year"),
    # --- Previously failing ---
    ("GM space dollar", "The salary range for this role is $ 269,400.00 to $412,600.00", 269400, 412600, "year"),
    ("Headway space", "expected base pay range for this position is $ 212,000 - $265,000", 212000, 265000, "year"),
    ("BlastPoint", "Salary Range: $140-160K", 140000, 160000, "year"),
    ("EvolutionIQ", "The base salary range is $185-235K", 185000, 235000, "year"),
    ("Zip Co 000K", "The annual base Pay Range for this position is $150,000- 161,000K", 150000, 161000, "year"),  # 161,000K = 161M, actually bad data
    ("TMX /year K", "Salary Range: ...$125,000/year - $140,000K/year USD", 125000, 140000, "year"),  # also bad data
    ("Sanofi EU", "The salary range for this position is: $122.250,00 - $176.583,33", 122250, 176583, "year"),
    # --- Phase 1 RCA regression fixtures ---
    # &mdash; separator (dominant May-14 pattern — all Greenhouse jobs use this)
    ("mdash range Recorded Future", "The pay range for this full-time position is $ 167,000-$234,000. Our salary ranges are determined by role.", 167000, 234000, "year"),
    ("mdash range StubHub", "Salary Range $180,000 — $250,000 USD About Us StubHub is the world's leading marketplace.", 180000, 250000, "year"),
    ("mdash range Datavant", "The estimated total cash compensation range for this role is: $190,000 — $240,000 USD", 190000, 240000, "year"),
    ("mdash range HubSpot", "Annual Cash Compensation Range: $259,600 — $415,400 USD", 259600, 415400, "year"),
    ("mdash range Airbnb", "Pay Range $136,000 — $160,000 USD", 136000, 160000, "year"),
    ("mdash range Anduril", "US Salary Range $191,000 — $253,000 USD The salary range for this role is an estimate.", 191000, 253000, "year"),
    ("mdash range Lila Sciences", "Expected Base Salary Range $360,000 — $570,000 USD About LILA", 360000, 570000, "year"),
    ("mdash range Roblox", "Annual Salary Range $345,040 — $399,420 USD Roles that are based in an office", 345040, 399420, "year"),
    ("mdash range Axon", "Base Pay Range $84,750 — $135,600 USD Don't meet every single requirement?", 84750, 135600, "year"),
    ("mdash range DoorDash", "including Illinois and Colorado. $78,200 — $115,000 USD About DoorDash", 78200, 115000, "year"),
    ("mdash range Samsara", "Annual Base Salary $148,680 — $265,500 USD Total Rewards", 148680, 265500, "year"),
    ("mdash range Unity CAD", "Gross base salary $152,900 — $206,400 CAD", 152900, 206400, "year"),
    ("mdash range Waymo", "Salary Range $272,000 — $336,000 USD", 272000, 336000, "year"),
    # k-notation
    ("Gong k-notation", "The pay range for this position is $220k - $265k USD.", 220000, 265000, "year"),
    ("Together AI K range", "The pay range for this full-time position is: $210-250K + equity + benefits.", 210000, 250000, "year"),
    # Space after dollar (Taskrabbit, DriveWealth)
    ("Taskrabbit space dollar range", "The pay range for this position is $ 127,000 - 170,000.", 127000, 170000, "year"),
    ("DriveWealth Pay Range USD", "Pay Range : $200,000 USD - $225,000 USD", 200000, 225000, "year"),
    # Square bracket range (LVT)
    ("LVT bracket range", "The salary range for this role is [$140,000 - $190,000] USD", 140000, 190000, "year"),
    # Bracket range — _try_labeled_range or _try_bare_range should catch via window
    # Estimate phrasing (Octus)
    ("Octus estimate", "The base salary estimate for this position is $150,000 - $160,000.", 150000, 160000, "year"),
    # "to" separator (Careaccess, Postman OTE)
    ("Careaccess to separator", "The salary range for this role is $180,000 - $220,000 USD per year for full time team members.", 180000, 220000, "year"),
    ("Postman OTE range", "The compensation range for the role is $137,000 to $170,000 OTE, plus a competitive equity package.", 137000, 170000, "year"),
    # Gross pay (Unity)
    ("Unity gross pay", "Gross pay salary $100,800 — $159,600 USD", 100800, 159600, "year"),
    # Zone pay ranges (Databricks)
    ("Databricks zone pay", "Zone 2 Pay Range $219,100 — $301,300 USD Zone 3 Pay Range $219,100 — $301,300 USD", 219100, 301300, "year"),
    # Phase 1 regex-gap fixes
    # MFS: single value blocked by distant "salary range" — guard scope fix
    ("MFS single value distant range guard", "Base Salary: $ 115,000 This position is eligible for competitive incentive bonus. At MFS, we believe in fair and transparent compensation. For that reason, we're including the salary range for this position in a separate section.", 115000, 115000, "year"),
    # Raymond James: SALARY colon single value with "range" far away
    ("Raymond James SALARY colon", "SALARY : $137,000 per year Education Work Experience. The total compensation for this position includes base salary or wages, and may include other components.", 137000, 137000, "year"),
    # Comcast: Base Pay single hourly value
    ("Comcast Base Pay hourly", "Compensation Base Pay: $32.00 Base pay is one part of the Total Rewards that Comcast provides.", 32, 32, "hour"),
    # Non-salary monetary ranges must not become compensation.
    ("funding range", "We raised $100,000 - $200,000 in seed funding last year.", None, None, None),
    ("budget range", "You will manage a $150,000 - $250,000 annual marketing budget.", None, None, None),
    ("bonus range", "Annual performance bonus ranges from $20,000 - $40,000.", None, None, None),
    ("equity value range", "Equity grant value is $80,000 - $120,000 over four years.", None, None, None),
    ("no disclosed amount", "We offer a competitive salary and equity package.", None, None, None),
]

passed = failed = 0
for name, text, exp_min, exp_max, exp_period in TESTS:
    lo, hi, period = parse_salary_range(text, skip_llm=True)
    if exp_min is None:
        ok = lo is None and hi is None and period is None
    else:
        ok = (
            lo is not None and
            abs(float(lo) - float(exp_min)) < 1 and
            abs(float(hi) - float(exp_max)) < 1 and
            period == exp_period
        )
    if ok:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}")
        print(f"     expected: ({exp_min}, {exp_max}, {exp_period})")
        print(f"     got:      ({lo}, {hi}, {period})")

print(f"\n{passed}/{passed+failed} passing")
