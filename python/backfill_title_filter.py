#!/usr/bin/env python3
"""
backfill_title_filter.py

Whitelist-based title filter backfill.

A job is KEPT only if its title matches at least one knowledge-worker inclusion
pattern AND does not trigger any hard-block exclusion pattern.

Usage:
    python python/backfill_title_filter.py              # dry-run (default)
    python python/backfill_title_filter.py --apply      # write to DB
    python python/backfill_title_filter.py --spot N     # random sample size (default 30)
"""

import argparse
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))

BATCH_SIZE = 500

# ── Whitelist filter (must stay in sync with ingest_jobs.py) ──────────────────

_KW_INCLUDE_RE = re.compile("|".join([
    # DATA / ML
    r"\bdata\s+scienc",
    r"\bdata\s+engineer",
    r"\bdata\s+analyst",
    r"\bdata\s+architect",
    r"\bdata\s+steward",
    r"\bdataops\b|\bdata\s+ops\b",
    r"\bdata\s+platform\b",
    r"\bml\s+engineer",
    r"\bml\s+researcher",
    r"\bmachine\s+learning\b",
    r"\bapplied\s+scientist",
    r"\bresearch\s+scientist",
    r"\bai\s+engineer",
    r"\bai\s+researcher",
    r"\bai/ml\b",
    r"\banalytics\s+engineer",
    r"\bbi\s+(?:analyst|engineer|developer)\b",
    r"\bbusiness\s+intelligence\b",
    r"\bmlops\b|\bml\s+ops\b",
    r"\bdecision\s+scientist",
    r"\bquantitative\s+analyst\b|\bquant\s+analyst\b|\bquant\b",
    r"\bactuar",
    r"\bmarketing\s+analyst\b",
    r"\bproduct\s+analyst\b",
    r"\bfinancial\s+analyst\b",
    # ENGINEERING
    r"\bsoftware\s+engin",
    r"\bsoftware\s+develop",
    r"\bsde\b|\bswe\b",
    r"\bbackend\b|\bback[\s-]end\b",
    r"\bfrontend\b|\bfront[\s-]end\b",
    r"\bfull[\s-]?stack\b",
    r"\bdeveloper\b",
    r"\bdevops\b",
    r"\bsite\s+reliability\b",
    r"\bsre\b",
    r"\bplatform\s+engineer",
    r"\binfrastructure\s+engineer",
    r"\bcloud\s+engineer",
    r"\bsystems\s+engineer",
    r"\bios\b|\bandroid\b",
    r"\bmobile\s+(?:engineer|developer)",
    r"\bsecurity\s+engineer",
    r"\bappsec\b|\bapplication\s+security\b",
    r"\bqa\s+engineer\b|\bsdet\b|\btest\s+engineer\b|\bautomation\s+engineer\b",
    r"\bembedded\s+(?:engineer|software)\b|\bfirmware\s+(?:engineer|developer)\b",
    r"\bprincipal\s+engineer\b|\bstaff\s+engineer\b|\bdistinguished\s+engineer\b",
    r"\bengineering\s+manager\b|\bdirector\s+of\s+engineering\b",
    r"\brobotic",
    r"\bcontrols\s+engineer\b",
    # SALES
    r"\baccount\s+executive\b",
    r"\bsdr\b|\bbdr\b",
    r"\bsales\s+development\s+rep",
    r"\bbusiness\s+development\s+rep",
    r"\bcustomer\s+success\b",
    r"\bcsm\b",
    r"\bsales\s+engineer\b",
    r"\bsolutions\s+engineer\b",
    r"\bsolutions\s+architect\b",
    r"\baccount\s+manager\b",
    r"\brenewals\s+manager\b",
    r"\brevenue\s+ops\b|\brevops\b|\bsales\s+ops\b",
    r"\bvp\s+(?:of\s+)?sales\b|\bhead\s+of\s+sales\b|\bchief\s+revenue\b|\bcro\b",
    r"\bsales\s+director\b|\bregional\s+sales\s+manager\b",
    # FINANCE
    r"\bfp&a\b|\bfinancial\s+planning\b",
    r"\bfinancial\s+(?:analyst|reporting|controller|manager)\b",
    r"\baccountant\b|\baccounting\b|\bbookkeeper\b|\bcontroller\b",
    r"\btreasury\b|\btreasurer\b",
    r"\bauditor\b|\baudit\b",
    r"\btax\s+(?:analyst|manager|associate|director|counsel)\b",
    r"\binvestment\b",
    r"\bequity\s+research\b|\bcredit\s+analyst\b",
    r"\bunderwriter\b",
    r"\bcfo\b|\bvp\s+(?:of\s+)?finance\b|\bfinance\s+director\b",
    # MARKETING
    r"\bmarketing\s+(?:manager|engineer|ops|director|coordinator|specialist|lead)\b",
    r"\bgrowth\s+(?:manager|lead|hacker|marketing|analyst|engineer|ops)\b|\bhead\s+of\s+growth\b",
    r"\blifecycle\b",
    r"\bperformance\s+marketing\b",
    r"\bdemand\s+gen(?:eration)?\b",
    r"\bcontent\s+(?:marketing|strategist|manager|creator|writer|producer|director)\b",
    r"\bcopywriter\b",
    r"\bcreative\s+director\b|\bart\s+director\b",
    r"\bpublic\s+relations\b",
    r"\bcommunications?\s+(?:manager|director|specialist|strategist)\b",
    r"\bsocial\s+media\s+(?:manager|strategist|analyst)\b",
    r"\bseo\b|\bsem\b",
    r"\bpaid\s+(?:acquisition|media|search|social)\b",
    r"\bproduct\s+marketing\b|\bpmm\b",
    r"\bmarketing\s+ops\b|\bmarops\b",
    r"\bvp\s+(?:of\s+)?marketing\b|\bcmo\b|\bhead\s+of\s+marketing\b",
    r"\bbrand\b",
    # PRODUCT
    r"\bproduct\s+manager\b|\bproduct\s+owner\b",
    r"\btpm\b|\btechnical\s+product\s+manager\b",
    r"\bproduct\s+ops\b",
    r"\bprincipal\s+pm\b|\bgroup\s+pm\b|\bhead\s+of\s+product\b",
    r"\bvp\s+(?:of\s+)?product\b|\bcpo\b",
    # DESIGN
    r"\bdesigner\b",
    r"\bux\b",
    r"\bui/ux\b|\bux/ui\b",
    r"\buser\s+(?:experience|interface)\b",
    r"\bdesign\b",
    # OPS
    r"\bbusiness\s+operations\b|\bbizops\b|\bbiz\s+ops\b",
    r"\bstrategic\s+(?:operations|ops)\b",
    r"\bpeople\s+ops\b|\bhr\s+ops\b|\bpeople\s+operations\b",
    r"\btalent\s+acquisition\b",
    r"\brecruiter\b|\brecruiting\b",
    r"\bchief\s+of\s+staff\b",
    r"\bprogram\s+manager\b",
    r"\boperations\s+analyst\b",
    r"\bexecutive\s+assistant\b",
    # BROAD CROSS-FUNCTIONAL
    r"\banalyst\b",
    r"\barchitect\b",
    r"\bscientist\b",
    r"\bresearcher\b",
    r"\bdirector\b",
    r"\bvp\b",
    r"\bhead\s+of\b",
    r"\bconsultant\b",
    r"\bstrateg",
    r"\bcompliance\b",
    r"\blegal\b",
    r"\bcounsel\b",
    # ADDITIONAL (gap-fill from dry-run review)
    r"\bai\b",
    r"\bllm\b",
    r"\bdba\b|\bdatabase\s+admin",
    r"\bfounding\b",
    r"\bsales\s+manager\b",
    r"\bsales\s+(?:representative|rep)\b",
    r"\bproject\s+manager\b",
    r"\bgtm\b",
    r"\btools\s+engineer\b|\btooling\s+engineer\b",
    r"\bcompiler\s+engineer\b",
    r"\bagile\b",
    r"\bscrum\b",
    r"\bsales\s+operat",
    r"\bsecurity\s+manager\b",
    r"\bproduct\s+lead\b",
    r"\bresearch\b",
]), re.IGNORECASE)

_KW_EXCLUDE_RE = re.compile("|".join([
    r"\bdriver\b",
    r"\bcourier\b",
    r"\bpizza\b",
    r"\bbarista\b",
    r"\bcashier\b",
    r"\bstocker\b",
    r"\bhostess\b",
    r"\bline\s+cook\b|\bprep\s+cook\b|\bdishwasher\b",
    r"\bfood\s+service\b",
    r"\bwarehouse\s+(?:associate|worker|team\s+member|operator)\b",
    r"\bretail\s+associate\b|\bstore\s+associate\b|\bsales\s+associate\b",
    r"\bshift\s+(?:leader|manager)\b",
    r"\bphysical\s+therapist\b|\boccupational\s+therapist\b|\bspeech\s+therapist\b",
    r"\blicensed\s+therapist\b|\bmental\s+health\s+therapist\b",
    r"\bregistered\s+nurse\b|\bnurse\s+practitioner\b|\bnurse\s+anesthetist\b",
    r"\brn\b|\blpn\b|\bcna\b|\blvn\b",
    r"\bmedical\s+assistant\b|\bpatient\s+services\b",
    r"\bpharmacy\s+technician\b",
    r"\bautomotive\s+painter\b|\bauto\s+body\b|\bwheel\s+repair\b",
    r"\bdiesel\s+technician\b|\bmechanic\b",
    r"\bfacilities\s+technician\b|\bmaintenance\s+technician\b|\bhvac\b",
    r"\belectrician\b|\bplumber\b|\bwelder\b|\bcarpenter\b",
    r"\btest\s+driver\b|\bvehicle\s+operator\b",
    r"\bdata\s+collection\s+driver\b|\bmapping\s+data\s+collection\b",
    r"\bfield\s+(?:technician|service\s+technician)\b",
    r"\bconstruction\b|\blaborer\b",
    r"\bclinical\s+research\s+coordinator\b",
]), re.IGNORECASE)


def _is_knowledge_worker_title(title: str) -> bool:
    if not title:
        return False
    if not _KW_INCLUDE_RE.search(title):
        return False
    if _KW_EXCLUDE_RE.search(title):
        return False
    return True


def _matched_inclusion(title: str) -> str:
    m = _KW_INCLUDE_RE.search(title)
    return m.group(0) if m else "?"


def _matched_exclusion(title: str) -> str:
    m = _KW_EXCLUDE_RE.search(title)
    return m.group(0) if m else "?"


# ── DB ─────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        dbname="job_analytics",
    )


# ── Specific test cases (always shown) ────────────────────────────────────────

TEST_CASES = [
    # (title, expected_result)
    # Core cases
    ("Mapping Data Collection Driver",              False),
    ("Senior Software Engineer, Self-Driving",      True),
    ("Mechanical Engineer",                         False),
    ("Data Science Intern",                         True),
    ("PhD Research Scientist",                      True),
    ("Survey Automation & Delivery Analyst",        True),
    ("Delivery Solutions Architect",                True),
    ("Mental Health Therapist",                     False),
    ("Fleet Operations Technician",                 False),
    ("Autonomous Vehicle Test Driver",              False),
    ("Director of Engineering, Robotics",           True),
    ("Compliance Manager, Banking",                 True),
    # Previously false-positives — must now be KEPT
    ("AI Deployment Engineer, Codex",               True),
    ("Compiler Engineer - Algorithmic Workloads",   True),
    ("Founding Engineer",                           True),
    ("Discovery Database Administrator (DBA)",      True),
    ("GTM Engineer, Marketing Operations",          True),
    ("Sales Manager, Enterprise",                   True),
    ("Manager of Sales Operations & Performance",   True),
    ("Sr. Validation & Tools Engineer - Dev Tools", True),
    ("Senior eDiscovery Project Manager",           True),
    # Round 3 additions
    ("Sr. Enterprise Network Security Manager",     True),
    ("Sr. HR Technology Product Lead",              True),
    ("Research Intern, Special Projects",           True),
    # Must still be DROPPED
    ("Pizza Maker/Crew Member",                     False),
    ("Maintenance Technician",                      False),
    ("Registered Nurse, ICU",                       False),
]


# ── Main ───────────────────────────────────────────────────────────────────────

def run(apply: bool, spot: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT jp.job_id, r.role_name AS title
        FROM job_postings jp
        LEFT JOIN roles r ON r.role_id = jp.role_id
        WHERE jp.status = 'raw' AND jp.data_tier = 1
    """)
    jobs = cur.fetchall()
    total = len(jobs)
    print(f"Loaded {total:,} raw tier-1 survivors")

    kept_ids: list[str] = []
    drop_ids: list[str] = []
    kept_samples: list[tuple[str, str]] = []   # (title, matched_inclusion)
    drop_samples: list[tuple[str, str]] = []   # (title, no_inclusion OR exclusion_hit)

    for job in jobs:
        title = (job["title"] or "").strip()
        if _is_knowledge_worker_title(title):
            kept_ids.append(job["job_id"])
            if len(kept_samples) < 5000:
                kept_samples.append((title, _matched_inclusion(title)))
        else:
            drop_ids.append(job["job_id"])
            if len(drop_samples) < 5000:
                inc = _KW_INCLUDE_RE.search(title)
                if inc:
                    reason = f"exclusion={_matched_exclusion(title)}"
                else:
                    reason = "no_inclusion"
                drop_samples.append((title, reason))

    pct_kept = 100 * len(kept_ids) / total if total else 0
    pct_drop = 100 * len(drop_ids) / total if total else 0

    print(f"\n{'='*65}")
    print(f"TITLE WHITELIST DRY-RUN  ({total:,} survivors)")
    print(f"{'='*65}")
    print(f"  KEPT (knowledge worker): {len(kept_ids):>7,}  ({pct_kept:.1f}%)")
    print(f"  DROP (→ ignored):        {len(drop_ids):>7,}  ({pct_drop:.1f}%)")

    # ── Test cases ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("SPECIFIC TEST CASES")
    print(f"{'─'*65}")
    all_pass = True
    for title, expected in TEST_CASES:
        actual = _is_knowledge_worker_title(title)
        status = "PASS" if actual == expected else "FAIL"
        if actual != expected:
            all_pass = False
        inc_m = _matched_inclusion(title) if _KW_INCLUDE_RE.search(title) else "—"
        exc_m = _matched_exclusion(title) if _KW_EXCLUDE_RE.search(title) else "—"
        result_label = "KEPT" if actual else "DROP"
        print(f"  [{status}] {result_label:<4}  {title[:52]:<52}  inc={inc_m}  exc={exc_m}")
    if all_pass:
        print("  All test cases passed.")

    # ── Spot-check: random KEPT ────────────────────────────────────────────────
    sample_kept = random.sample(kept_samples, min(spot, len(kept_samples)))
    sample_kept.sort(key=lambda x: x[0].lower())
    print(f"\n{'─'*65}")
    print(f"SPOT-CHECK: {len(sample_kept)} random KEPT  (title → matched inclusion)")
    print(f"{'─'*65}")
    for title, inc in sample_kept:
        print(f"  {title[:55]:<55} ← [{inc}]")

    # ── Spot-check: random DROPPED ────────────────────────────────────────────
    sample_drop = random.sample(drop_samples, min(spot, len(drop_samples)))
    sample_drop.sort(key=lambda x: x[0].lower())
    print(f"\n{'─'*65}")
    print(f"SPOT-CHECK: {len(sample_drop)} random DROP  (title → reason)")
    print(f"{'─'*65}")
    for title, reason in sample_drop:
        print(f"  {title[:55]:<55} ← [{reason}]")

    if not apply:
        print(f"\nDry-run — no DB changes.")
        print(f"Re-run with --apply to mark {len(drop_ids):,} jobs as 'ignored'.")
        return

    # ── Apply ──────────────────────────────────────────────────────────────────
    print(f"\nApplying {len(drop_ids):,} → status='ignored' ...")
    start = time.time()
    updated = 0
    for i in range(0, len(drop_ids), BATCH_SIZE):
        batch = drop_ids[i : i + BATCH_SIZE]
        cur.execute(
            "UPDATE job_postings SET status = 'ignored' WHERE job_id = ANY(%s)",
            (batch,),
        )
        conn.commit()
        updated += len(batch)
        if updated % 5000 == 0 or updated == len(drop_ids):
            elapsed = time.time() - start
            print(f"  {updated:,} / {len(drop_ids):,}  ({elapsed:.0f}s elapsed)")

    print(f"\nDone.")
    print(f"  {len(drop_ids):,} jobs → status='ignored'")
    print(f"  {len(kept_ids):,} jobs remain status='raw'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Whitelist title backfill: keep only knowledge-worker roles"
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to DB (default: dry-run)")
    parser.add_argument("--spot", type=int, default=30, metavar="N",
                        help="Random spot-check sample size per category (default: 30)")
    args = parser.parse_args()
    run(apply=args.apply, spot=args.spot)
