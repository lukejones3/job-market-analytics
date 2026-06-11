#!/usr/bin/env python3
"""Boilerplate-stripped embeddings for visible jobs.

For each company, identifies text chunks appearing across multiple JDs
(company boilerplate, benefits, EEO, etc.) and removes them before embedding.
Keeps only role-specific content.

Embeds any job where status='raw' AND data_tier=1 AND domain IS NOT NULL AND embedding IS NULL.
Safe to run repeatedly; only processes missing embeddings.
"""

import os
import time
import re
from collections import Counter, defaultdict
import psycopg2
from psycopg2.extras import execute_batch
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / '.env')

BATCH_SIZE = 32
MAX_TEXT_CHARS = 2500
MIN_JOBS_FOR_DEDUP = 3
CHUNK_SIZE = 150
BOILERPLATE_THRESHOLD = 0.40
MODEL_NAME = 'all-MiniLM-L6-v2'

def get_conn():
    return psycopg2.connect(
        host=os.environ['PGHOST'],
        port=os.environ.get('PGPORT', '5432'),
        dbname=os.environ['PGDATABASE'],
        user=os.environ['PGUSER'],
        password=os.environ['PGPASSWORD'],
    )

def normalize_chunk(text):
    return re.sub(r'\s+', ' ', text.lower().strip())

def chunkify(text, size=CHUNK_SIZE):
    if not text:
        return []
    chunks = []
    step = size // 2
    for i in range(0, len(text), step):
        chunk = text[i:i+size]
        if len(chunk) >= size // 2:
            chunks.append(chunk)
    return chunks

def find_boilerplate(descriptions):
    if len(descriptions) < MIN_JOBS_FOR_DEDUP:
        return set()
    chunk_doc_count = Counter()
    for desc in descriptions:
        chunks_in_doc = set()
        for chunk in chunkify(desc or ''):
            normalized = normalize_chunk(chunk)
            if len(normalized) >= 50:
                chunks_in_doc.add(normalized)
        for chunk in chunks_in_doc:
            chunk_doc_count[chunk] += 1
    threshold = max(2, int(len(descriptions) * BOILERPLATE_THRESHOLD))
    return {chunk for chunk, count in chunk_doc_count.items() if count >= threshold}

def strip_boilerplate(text, boilerplate_set):
    if not text or not boilerplate_set:
        return text or ''
    keep = [True] * len(text)
    text_lower = text.lower()
    for chunk in boilerplate_set:
        chunk_words = chunk.split()
        if len(chunk_words) < 5:
            continue
        anchor = ' '.join(chunk_words[:8])
        try:
            pattern = re.escape(anchor).replace(r'\ ', r'\s+')
            for m in re.finditer(pattern, text_lower, re.IGNORECASE):
                start = m.start()
                end = min(start + len(chunk) + 50, len(text))
                for i in range(start, end):
                    keep[i] = False
        except re.error:
            continue
    kept = ''.join(c for c, k in zip(text, keep) if k)
    return re.sub(r'\s+', ' ', kept).strip()

def build_text(title, description, boilerplate_set):
    title_clean = (title or '')[:200]
    desc_stripped = strip_boilerplate(description or '', boilerplate_set)
    desc_clean = desc_stripped[:MAX_TEXT_CHARS - len(title_clean) - 2]
    return f"{title_clean}. {desc_clean}"

def main():
    print(f"Loading model {MODEL_NAME}...", flush=True)
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME)
    model.max_seq_length = 256
    print(f"Model loaded in {time.time()-t0:.1f}s", flush=True)

    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM job_postings jp
            WHERE jp.status='raw' AND jp.data_tier=1
              AND jp.domain IS NOT NULL AND jp.embedding IS NULL
        """)
        missing = cur.fetchone()[0]
    print(f"Jobs needing embeddings: {missing:,}", flush=True)

    if missing == 0:
        print("Nothing to do.", flush=True)
        return

    # For boilerplate detection: fetch ALL active company descriptions
    # (not just missing) so detection is accurate
    print("Fetching company descriptions for boilerplate detection...", flush=True)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT jp.company_id, COALESCE(jp.description_text, '')
            FROM job_postings jp
            WHERE jp.status='raw' AND jp.data_tier=1 AND jp.domain IS NOT NULL
        """)
        all_descs = cur.fetchall()

    company_descs = defaultdict(list)
    for company_id, desc in all_descs:
        company_descs[company_id].append(desc)

    print(f"Computing boilerplate for {len(company_descs):,} companies...", flush=True)
    t0 = time.time()
    boilerplate_by_company = {
        cid: find_boilerplate(descs) for cid, descs in company_descs.items()
    }
    bp_companies = sum(1 for bp in boilerplate_by_company.values() if bp)
    print(f"Boilerplate detection done in {time.time()-t0:.1f}s ({bp_companies:,} companies had boilerplate)", flush=True)

    # Now process only jobs missing embeddings
    processed = 0
    start = time.time()

    while True:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT jp.job_id, jp.company_id, r.role_name, COALESCE(jp.description_text, '')
                FROM job_postings jp
                JOIN roles r ON r.role_id = jp.role_id
                WHERE jp.status='raw' AND jp.data_tier=1
                  AND jp.domain IS NOT NULL AND jp.embedding IS NULL
                ORDER BY jp.ingested_at DESC
                LIMIT %s
            """, (BATCH_SIZE,))
            rows = cur.fetchall()
        conn.commit()

        if not rows:
            break

        texts = [
            build_text(title, desc, boilerplate_by_company.get(company_id, set()))
            for (_, company_id, title, desc) in rows
        ]

        batch_start = time.time()
        embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False, convert_to_numpy=True)
        batch_time = time.time() - batch_start

        updates = [(emb.tolist(), job_id) for (job_id, _, _, _), emb in zip(rows, embeddings)]
        with conn.cursor() as cur:
            execute_batch(cur, "UPDATE job_postings SET embedding = %s WHERE job_id = %s", updates, page_size=BATCH_SIZE)
        conn.commit()

        processed += len(rows)
        elapsed = time.time() - start
        rate = processed / elapsed if elapsed > 0 else 0
        eta = (missing - processed) / rate if rate > 0 else 0

        print(f"  {processed:,}/{missing:,} ({100*processed/missing:.1f}%) - {rate:.1f} jobs/sec - batch {batch_time:.1f}s - ETA {eta/60:.1f} min", flush=True)

    print(f"\nDone. Embedded {processed:,} jobs in {(time.time()-start)/60:.1f} min", flush=True)
    conn.close()

if __name__ == '__main__':
    main()
