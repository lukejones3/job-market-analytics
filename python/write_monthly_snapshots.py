import os
import psycopg2
from psycopg2.extras import DictCursor

from snapshots import write_monthly_skill_snapshot

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def main():
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(
                """
                SELECT run_id
                FROM pipeline_runs
                WHERE status = 'success'
                ORDER BY started_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                print("[snapshot] No successful pipeline run found in pipeline_runs.")
                return

            run_id = row["run_id"]
            rows = write_monthly_skill_snapshot(cur, run_id)
            conn.commit()
            print(f"[snapshot] OK run_id={run_id} rows_written≈{rows}")

    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
