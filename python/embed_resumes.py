#!/usr/bin/env python3
"""Embed resumes for semantic job matching."""

import os
import time
import psycopg2
from psycopg2.extras import execute_batch
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / '.env')

BATCH_SIZE = 16
MAX_TEXT_CHARS = 2500
MODEL_NAME = 'all-MiniLM-L6-v2'

def get_conn():
    return psycopg2.connect(
        host=os.environ['PGHOST'],
        port=os.environ.get('PGPORT', '5432'),
        dbname=os.environ['PGDATABASE'],
        user=os.environ['PGUSER'],
        password=os.environ['PGPASSWORD'],
    )

def main():
    print(f"Loading model {MODEL_NAME}...", flush=True)
    model = SentenceTransformer(MODEL_NAME)
    model.max_seq_length = 256
    print("Model loaded", flush=True)

    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM resumes
            WHERE embedding IS NULL
              AND resume_text IS NOT NULL
              AND length(resume_text) > 100
              AND status NOT IN ('parse_failed', 'failed')
        """)
        missing = cur.fetchone()[0]
    print(f"Resumes needing embeddings: {missing:,}", flush=True)

    if missing == 0:
        print("Nothing to do.", flush=True)
        return

    processed = 0
    start = time.time()

    while True:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT resume_id, COALESCE(resume_text, '')
                FROM resumes
                WHERE embedding IS NULL
                  AND resume_text IS NOT NULL
                  AND length(resume_text) > 100
                  AND status NOT IN ('parse_failed', 'failed')
                ORDER BY uploaded_at DESC
                LIMIT %s
            """, (BATCH_SIZE,))
            rows = cur.fetchall()
        conn.commit()

        if not rows:
            break

        texts = [text[:MAX_TEXT_CHARS] for (_, text) in rows]
        embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False, convert_to_numpy=True)

        updates = [(emb.tolist(), resume_id) for (resume_id, _), emb in zip(rows, embeddings)]
        with conn.cursor() as cur:
            execute_batch(cur, "UPDATE resumes SET embedding = %s WHERE resume_id = %s", updates, page_size=BATCH_SIZE)
        conn.commit()

        processed += len(rows)
        elapsed = time.time() - start
        print(f"  {processed:,}/{missing:,} - {processed/elapsed:.1f}/sec", flush=True)

    print(f"\nDone. Embedded {processed:,} resumes in {(time.time()-start):.1f}s", flush=True)
    conn.close()

if __name__ == '__main__':
    main()
