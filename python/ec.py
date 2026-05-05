#!/usr/bin/env python3
import csv, os, sys, time
from pathlib import Path
from collections import defaultdict

ROOT = Path("/opt/job-market-analytics")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "python"))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from python.llm_client import classify_role

EVAL_DIR = ROOT / "eval"
EVAL_INPUT = EVAL_DIR / "eval_set.csv"
EVAL_OUTPUT = EVAL_DIR / "eval_results.csv"

DATA_ML = {"data_analytics","data_engineering","ml_engineering","ai_research","data_science","analytics_engineering"}

def get_conn():
    return psycopg2.connect(
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        host=os.environ["PGHOST"],
        port=int(os.environ["PGPORT"]),
    )

def fetch_jobs(ids):
    if not ids: return {}
    with get_conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT jp.job_id, co.company_name, r.role_name, jp.description_text, jp.role_category AS current_category
            FROM job_postings jp
            JOIN companies co ON co.company_id = jp.company_id
            JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.job_id = ANY(%s)
        """, (ids,))
        return {row["job_id"]: row for row in cur.fetchall()}

def is_match(expected, pred_cat, pred_is_dml):
    e = (expected or "").strip().lower()
    if e == "data_ml": return bool(pred_is_dml) and pred_cat in DATA_ML
    if e == "non_data": return pred_cat == "non_data"
    if e in DATA_ML: return pred_cat == e
    return False

def main():
    if not EVAL_INPUT.exists():
        print(f"ERROR: {EVAL_INPUT} not found"); sys.exit(1)
    EVAL_DIR.mkdir(exist_ok=True)

    rows = []
    with open(EVAL_INPUT) as f:
        for r in csv.DictReader(f):
            jid = (r.get("job_id") or "").strip()
            exp = (r.get("expected") or "").strip()
            if jid and exp: rows.append({"job_id":jid,"expected":exp})

    print(f"Loaded {len(rows)} eval rows")
    jobs = fetch_jobs([r["job_id"] for r in rows])
    missing = [r["job_id"] for r in rows if r["job_id"] not in jobs]
    if missing: print(f"WARNING: {len(missing)} not in DB: {missing[:5]}")

    print(f"\nRunning classify_role on {len(jobs)} jobs...\n" + "="*80)
    results = []; start = time.time()
    for i, r in enumerate(rows, 1):
        jid, exp = r["job_id"], r["expected"]
        if jid not in jobs:
            results.append({"job_id":jid,"company":"","title":"","expected":exp,"predicted":"MISSING","is_data_ml":"","confidence":"","reason":"not in DB","match":"N"})
            continue
        j = jobs[jid]; title = j["role_name"]; desc = j["description_text"] or ""
        try:
            t0 = time.time(); v = classify_role(title, desc, company_name=j["company_name"]); el = time.time()-t0
        except Exception as e:
            v = None; el = 0; print(f"  [{i}] {jid} EXCEPTION: {e}")
        if v is None:
            results.append({"job_id":jid,"company":j["company_name"],"title":title,"expected":exp,"predicted":"API_FAIL","is_data_ml":"","confidence":"","reason":"None returned","match":"N"})
            print(f"  [{i}/{len(rows)}] {jid} {j['company_name'][:25]:<25} | API FAILED")
            continue
        pred = v.get("category","?"); dml = v.get("is_data_ml",False); conf = v.get("confidence","?"); reason = v.get("reason","")
        m = "Y" if is_match(exp, pred, dml) else "N"
        results.append({"job_id":jid,"company":j["company_name"],"title":title,"expected":exp,"predicted":pred,"is_data_ml":str(dml),"confidence":conf,"reason":reason,"match":m})
        mk = "✓" if m=="Y" else "✗"
        print(f"  [{i}/{len(rows)}] {mk} {jid} {j['company_name'][:25]:<25} | exp={exp:<20} pred={pred:<22} ({el:.1f}s)")

    print("="*80); print(f"Total: {time.time()-start:.1f}s")

    with open(EVAL_OUTPUT,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=["job_id","company","title","expected","predicted","is_data_ml","confidence","reason","match"])
        w.writeheader(); w.writerows(results)
    print(f"\nWrote: {EVAL_OUTPUT}")

    valid = [r for r in results if r["predicted"] not in ("MISSING","API_FAIL")]
    matches = sum(1 for r in valid if r["match"]=="Y")
    print("\n" + "="*80 + "\nSUMMARY\n" + "="*80)
    if valid: print(f"\nOverall: {matches}/{len(valid)} = {100*matches/len(valid):.1f}%")

    print("\n--- KEEP / KILL ---")
    kt=kr=lt=lr=0
    for r in valid:
        edml = r["expected"]=="data_ml" or r["expected"] in DATA_ML
        pdml = r["is_data_ml"].lower()=="true"
        if edml:
            kt += 1
            if pdml: kr += 1
        else:
            lt += 1
            if not pdml: lr += 1
    if kt: print(f"KEEP: {kr}/{kt} ({100*kr/kt:.1f}%) — {kt-kr} false negatives (REAL JOBS KILLED)")
    if lt: print(f"KILL: {lr}/{lt} ({100*lr/lt:.1f}%) — {lt-lr} false positives (junk leaked)")

    fails = [r for r in valid if r["match"]=="N"]
    if fails:
        print("\n--- FAILURES ---")
        for r in fails:
            print(f"  {r['job_id']} {r['company'][:25]:<25} | {r['title'][:45]}")
            print(f"    expected={r['expected']} predicted={r['predicted']} dml={r['is_data_ml']} conf={r['confidence']}")
            print(f"    reason: {r['reason']}\n")

    by_conf = defaultdict(lambda:[0,0])
    for r in valid:
        by_conf[r["confidence"]][1] += 1
        if r["match"]=="Y": by_conf[r["confidence"]][0] += 1
    if by_conf:
        print("--- accuracy by confidence ---")
        for c,(rt,tt) in sorted(by_conf.items()): print(f"  {c:<10} {rt}/{tt} ({100*rt/tt:.0f}%)")

    n = len(valid); cost = n * (800/1_000_000*1.0 + 100/1_000_000*5.0)
    print(f"\nEst cost: ~${cost:.4f} ({n} calls)")

if __name__ == "__main__":
    main()
