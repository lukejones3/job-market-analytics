#!/usr/bin/env python3
"""
Classify job_postings.experience_level_v2 for all jobs that have a stored embedding
but no v2 label yet.

Inference order: title-first junior rule → ML (stored embedding) → title override →
mid-zone. Low confidence → 'mid'. No LLM fallback.

Jobs without a stored embedding are skipped; they will be classified on the next run
after embed_jobs.py processes them.

Run daily after embed_jobs.py:
    /opt/job-market-analytics/.venv/bin/python python/classify_exp_level_v2.py --apply

Dry-run (default): prints counts without writing.
"""

import argparse
import logging
import os
import re
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

ARTIFACT_PATH = Path('/opt/job-market-analytics/models/exp_level_classifier.pkl')
BATCH_SIZE = 512

MID_CONF_LOW  = 0.40
MID_CONF_HIGH = 0.65

JUNIOR_RE = re.compile(
    r'\b(?:intern(?:ship)?|junior|graduate?|entry[\s\-]level|early[\s\-]career|'
    r'apprentice|trainee|new[\s\-]grad|university[\s\-]grad|campus)\b'
    r'|\bjr\.?(?=\W|$)',
    re.IGNORECASE,
)
SENIOR_RE = re.compile(
    r'\b(?:senior|staff|principal)\b|\bsr\.?(?=\W|$)',
    re.IGNORECASE,
)
LEAD_RE = re.compile(
    r'\b(?:lead|manager|director|vp|head\s+of)\b',
    re.IGNORECASE,
)
LEVEL_KW_RE = re.compile(
    r'\b(?:intern(?:ship)?|junior|graduate?|entry[\s\-]level|early[\s\-]career|'
    r'apprentice|trainee|new[\s\-]grad|campus|senior|staff|principal|'
    r'lead|manager|director|vp|head\s+of)\b'
    r'|\b(?:jr|sr)\.?(?=\W|$)',
    re.IGNORECASE,
)


def get_conn():
    return psycopg2.connect(
        host=os.environ['PGHOST'],
        port=os.environ.get('PGPORT', '5432'),
        dbname=os.environ['PGDATABASE'],
        user=os.environ['PGUSER'],
        password=os.environ['PGPASSWORD'],
    )


def parse_embedding(emb_str: str) -> np.ndarray:
    return np.fromstring(emb_str.strip('[]'), sep=',', dtype=np.float32)


def classify_jobs(conn, clf, apply: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT jp.job_id, r.role_name, jp.embedding
            FROM job_postings jp
            JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.status = 'raw' AND jp.data_tier = 1 AND jp.domain IS NOT NULL
              AND jp.experience_level_v2 IS NULL
              AND jp.embedding IS NOT NULL
            ORDER BY jp.ingested_at DESC
        """)
        rows = cur.fetchall()

    if not rows:
        log.info("No jobs need experience_level_v2 — nothing to do.")
        return

    log.info(f"Classifying {len(rows):,} jobs...")
    classes = list(clf.classes_)
    path_counts: Counter = Counter()
    updates = []

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        embs = np.array([parse_embedding(r[2]) for r in batch], dtype=np.float32)
        probas = clf.predict_proba(embs)

        for j, (job_id, title, _) in enumerate(batch):
            t = title or ''
            top_idx = int(np.argmax(probas[j]))
            top_class = classes[top_idx]
            top_conf = float(probas[j, top_idx])

            if JUNIOR_RE.search(t):
                v2, path = 'junior', 'title_junior'
            elif top_class == 'junior' and LEAD_RE.search(t):
                v2, path = 'lead', 'title_override'
            elif top_class == 'junior' and SENIOR_RE.search(t):
                v2, path = 'senior', 'title_override'
            else:
                has_kw = bool(LEVEL_KW_RE.search(t))
                if (top_class in ('junior', 'senior')
                        and MID_CONF_LOW <= top_conf <= MID_CONF_HIGH
                        and not has_kw):
                    v2, path = 'mid', 'mid_zone'
                elif top_conf < 0.50 and not has_kw:
                    v2, path = 'mid', 'mid_zone'
                else:
                    v2, path = top_class, 'classifier'

            path_counts[path] += 1
            updates.append((v2, job_id))

    dist = dict(Counter(v for v, _ in updates))
    log.info(f"Predicted distribution: {dist}")
    log.info("Path counts: " + ", ".join(
        f"{p}={n}" for p, n in sorted(path_counts.items(), key=lambda x: -x[1])
    ))

    if not apply:
        log.info(f"Dry-run: would write {len(updates):,} jobs. Pass --apply to commit.")
        return

    # Downstream API/marts use the canonical four-level vocabulary.  Make the
    # v2 classifier authoritative without leaking its training labels.
    canonical = {"junior": "entry", "lead": "senior"}
    canonical_updates = [(canonical.get(level, level), job_id) for level, job_id in updates]
    with conn.cursor() as cur:
        execute_batch(
            cur,
            "UPDATE job_postings SET experience_level_v2 = %s, experience_level = %s WHERE job_id = %s",
            [(level, level, job_id) for level, job_id in canonical_updates],
            page_size=500,
        )
    conn.commit()
    log.info(f"Wrote experience_level_v2 for {len(updates):,} jobs.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='Write predictions to DB (default: dry-run)')
    args = parser.parse_args()

    log.info(f"Loading classifier artifact from {ARTIFACT_PATH}...")
    artifact = joblib.load(ARTIFACT_PATH)
    clf = artifact['classifier']
    log.info(f"Classifier loaded: trained_at={artifact['trained_at']}, "
             f"class_weight={artifact.get('class_weight')}")

    conn = get_conn()
    classify_jobs(conn, clf, apply=args.apply)
    conn.close()
    log.info("Done.")


if __name__ == '__main__':
    main()
