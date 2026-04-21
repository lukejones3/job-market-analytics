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
]

passed = failed = 0
for name, text, exp_min, exp_max, exp_period in TESTS:
    lo, hi, period = parse_salary_range(text)
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
