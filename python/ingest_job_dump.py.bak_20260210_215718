import os
import re
from datetime import date
from typing import List, Tuple

import psycopg2
from psycopg2.extras import DictCursor


DUMP_PATH = os.getenv("JOB_DUMP_PATH", "data/job_dump.txt")


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def extract_jobs_from_dump(raw: str) -> List[str]:
    """
    Extract all job blocks between markers.
    Tolerates extra whitespace.
    """
    jobs = re.findall(r"===JOB START===\s*(.*?)\s*===JOB END===", raw, flags=re.S)
    # strip outer whitespace, drop empties
    jobs = [j.strip() for j in jobs if j and j.strip()]
    return jobs


def next_job_id(cur) -> str:
    """
    Job IDs look like J0001, J0059, etc.
    """
    cur.execute(
        "SELECT job_id FROM job_postings WHERE job_id ~ '^J[0-9]+$' ORDER BY job_id DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        n = 1
    else:
        last = row[0] if not isinstance(row, dict) else row["job_id"]
        n = int(re.sub(r"\D", "", last)) + 1
    return f"J{n:04d}"


def job_already_exists(cur, desc: str) -> bool:
    """
    Skip duplicates by exact description_text match.
    (Simple + safe. You can upgrade to hashes later.)
    """
    cur.execute(
        "SELECT 1 FROM job_postings WHERE description_text = %s LIMIT 1",
        (desc,),
    )
    return cur.fetchone() is not None


def insert_job(cur, job_id: str, desc: str) -> None:
    cur.execute(
        """
        INSERT INTO job_postings (job_id, source, date_found, status, description_text)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (job_id, "manual_dump", date.today(), "new", desc),
    )


def main():
    if not os.path.exists(DUMP_PATH):
        raise FileNotFoundError(f"Dump file not found: {DUMP_PATH}")

    raw = open(DUMP_PATH, "r", encoding="utf-8").read()
    jobs = extract_jobs_from_dump(raw)

    if not jobs:
        print("[WARN] No jobs found between markers. Nothing to do.")
        return

    conn = get_conn()
    conn.autocommit = False

    inserted_ids: List[str] = []
    skipped_duplicates = 0

    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            for idx, desc in enumerate(jobs, start=1):
                if job_already_exists(cur, desc):
                    skipped_duplicates += 1
                    continue

                job_id = next_job_id(cur)
                insert_job(cur, job_id, desc)
                inserted_ids.append(job_id)
                print(f"[OK] Inserted Job {idx} -> {job_id}")

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    print("--- Summary ---")
    print("Jobs detected in dump:", len(jobs))
    print("Inserted:", len(inserted_ids))
    if inserted_ids:
        for jid in inserted_ids:
            print(" ", jid)
    print("Skipped duplicates:", skipped_duplicates)
    print("Done: ingest only")
    print("Next: run enrich:")
    print("  python3 -m py_compile python/run_enrich_with_logging.py && python3 -u python/run_enrich_with_logging.py")


if __name__ == "__main__":
    main()

