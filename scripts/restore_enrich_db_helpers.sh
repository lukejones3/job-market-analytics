#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"
TS="$(date +%Y%m%d_%H%M%S)"
BK="${FILE}.bak.restore_db_helpers_${TS}"

cp "$FILE" "$BK"
echo "🧾 Backup: $BK"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# If helpers already exist, bail
if "def upsert_company" in s or "def upsert_role" in s or "def upsert_location" in s:
    print("ℹ️ DB helpers already present; skipping insert.")
    raise SystemExit(0)

anchor = re.search(r"(?m)^def\s+next_job_skill_id\s*\(", s)
if not anchor:
    raise SystemExit("❌ Could not find anchor: def next_job_skill_id(...).")

helpers = r'''
# -----------------------------
# DB helpers (RESTORED)
# -----------------------------

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def next_id(cur, table: str, id_col: str, prefix: str, width: int) -> str:
    cur.execute(
        f"SELECT {id_col} FROM {table} WHERE {id_col} LIKE %s ORDER BY {id_col} DESC LIMIT 1",
        (f"{prefix}%",),
    )
    row = cur.fetchone()
    if not row:
        n = 1
    else:
        last = row[0] if not isinstance(row, dict) else row[id_col]
        n = int(re.sub(r"\D", "", last)) + 1
    return f"{prefix}{n:0{width}d}"


def upsert_company(cur, company_name: str) -> str:
    cur.execute(
        "SELECT company_id FROM companies WHERE lower(company_name)=lower(%s) LIMIT 1",
        (company_name,),
    )
    row = cur.fetchone()
    if row:
        return row[0] if not isinstance(row, dict) else row["company_id"]

    new_id = next_id(cur, "companies", "company_id", "C", 3)
    cur.execute(
        "INSERT INTO companies (company_id, company_name) VALUES (%s, %s)",
        (new_id, company_name),
    )
    return new_id


def upsert_role(cur, role_name: str) -> str:
    cur.execute(
        "SELECT role_id FROM roles WHERE lower(role_name)=lower(%s) LIMIT 1",
        (role_name,),
    )
    row = cur.fetchone()
    if row:
        return row[0] if not isinstance(row, dict) else row["role_id"]

    new_id = next_id(cur, "roles", "role_id", "R", 3)
    archetype = infer_role_archetype(role_name)
    cur.execute(
        "INSERT INTO roles (role_id, role_name, role_archetype) VALUES (%s, %s, %s)",
        (new_id, role_name, archetype),
    )
    return new_id


def upsert_location(cur, location: str, state: Optional[str]) -> str:
    cur.execute(
        """
        SELECT location_id
        FROM locations
        WHERE lower(location)=lower(%s)
          AND ( (state IS NULL AND %s IS NULL) OR (state=%s) )
        LIMIT 1
        """,
        (location, state, state),
    )
    row = cur.fetchone()
    if row:
        return row[0] if not isinstance(row, dict) else row["location_id"]

    new_id = next_id(cur, "locations", "location_id", "L", 3)
    cur.execute(
        "INSERT INTO locations (location_id, location, state) VALUES (%s, %s, %s)",
        (new_id, location, state),
    )
    return new_id

'''.lstrip("\n")

s2 = s[:anchor.start()] + helpers + s[anchor.start():]
p.write_text(s2, encoding="utf-8")
print("✅ Restored DB helper functions: upsert_company / upsert_role / upsert_location")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
