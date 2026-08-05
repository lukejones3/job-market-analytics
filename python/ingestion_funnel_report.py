#!/usr/bin/env python3
"""Print and snapshot each ATS's path from active row to publishable job."""
import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main():
    conn = psycopg2.connect(
        host=os.environ["PGHOST"], port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )
    with conn, conn.cursor() as cur:
        cur.execute(Path("sql/ingestion_publication_funnel.sql").read_text())
        cur.execute("""SELECT ingestion_source, active_rows, tier1_rows,
            us_candidate_rows, classified_rows, unresolved_domain_rows,
            publishable_rows, latest_seen_at
            FROM ingestion_publication_funnel ORDER BY publishable_rows DESC""")
        rows = cur.fetchall()
    print("source               active   tier1  us-cand classified unresolved publishable latest")
    for row in rows:
        source, active, tier1, us, classified, unresolved, publishable, latest = row
        print(f"{(source or 'unknown'):<20} {active:>7} {tier1:>7} {us:>8} "
              f"{classified:>10} {unresolved:>10} {publishable:>11} {latest}")
    conn.close()


if __name__ == "__main__":
    main()
