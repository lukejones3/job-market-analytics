#!/usr/bin/env python3
"""Boilerplate-stripped embeddings for visible jobs.

For each company, identifies text chunks appearing across multiple JDs
(company boilerplate, benefits, EEO, etc.) and removes them before embedding.
Keeps only role-specific content.

Embeds any job where status='raw' AND data_tier=1 AND domain IS NOT NULL AND embedding IS NULL.
Safe to run repeatedly; only processes missing embeddings.

Memory/throughput notes (200k-corpus scale):
- Companies are processed one at a time (streamed), so peak memory is bounded
  by the largest single company's descriptions, not the whole corpus. The
  previous implementation loaded every description up front.
- Boilerplate sets are computed per company exactly as before, so stripped
  text (and therefore embedding semantics) is unchanged.
- Encoding runs in larger micro-batches (default 128 vs the old fixed 32);
  sentence-transformers parallelises inside each batch, so wider batches use
  the host's cores far better. Only jobs with embedding IS NULL are ever
  selected, so stored embeddings are never recomputed.
"""

import argparse
import os
import time
import re
from collections import Counter
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / '.env')

DEFAULT_BATCH_SIZE = 128
MAX_TEXT_CHARS = 2500
MIN_JOBS_FOR_DEDUP = 3
CHUNK_SIZE = 150
BOILERPLATE_THRESHOLD = 0.40
MODEL_NAME = 'all-MiniLM-L6-v2'

ELIGIBLE_WHERE = (
    "jp.status='raw' AND jp.data_tier=1 AND jp.embedding IS NULL"
)


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


def batched(items, size):
    """Yield successive list slices of at most `size` items."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    for start in range(0, len(items), size):
        yield list(items[start:start + size])


def load_model(model_name=MODEL_NAME):
    # Lazy import: keeps this module importable (and unit-testable) on hosts
    # without the sentence-transformers stack installed.
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    model.max_seq_length = 256
    return model


def companies_needing_embeddings(cur, limit=None):
    """Company ids with at least one eligible job still missing an embedding.

    Ordered by backlog size (largest first) so the biggest boilerplate sets —
    and the biggest publication wins — land first.
    """
    q = f"""
        SELECT jp.company_id, COUNT(*) AS missing
        FROM job_postings jp
        WHERE {ELIGIBLE_WHERE} AND jp.domain IS NOT NULL AND jp.company_id IS NOT NULL
        GROUP BY jp.company_id
        ORDER BY missing DESC, jp.company_id
    """
    if limit:
        q += " LIMIT %s"
        cur.execute(q, (limit,))
    else:
        cur.execute(q)
    return [row[0] for row in cur.fetchall()]


def count_missing(cur):
    cur.execute(f"""
        SELECT COUNT(*) FROM job_postings jp
        WHERE {ELIGIBLE_WHERE} AND jp.domain IS NOT NULL
    """)
    return cur.fetchone()[0]


def count_stranded_domain_null(cur):
    """Rows embed can never touch because domain is still NULL.

    These are the enrich/embed deadlock population: publication also requires
    domain, so they stay invisible until enrichment classifies them. We only
    report them here — embed_jobs must not guess a domain.
    """
    cur.execute(f"""
        SELECT COUNT(*) FROM job_postings jp
        WHERE {ELIGIBLE_WHERE} AND jp.domain IS NULL
    """)
    return cur.fetchone()[0]


def fetch_company_descriptions(cur, company_id):
    """All active descriptions for one company (boilerplate needs the full set)."""
    cur.execute("""
        SELECT COALESCE(jp.description_text, '')
        FROM job_postings jp
        WHERE jp.status='raw' AND jp.data_tier=1 AND jp.domain IS NOT NULL
          AND jp.company_id = %s
    """, (company_id,))
    return [row[0] for row in cur.fetchall()]


def fetch_company_jobs(cur, company_id):
    """Jobs for one company that still need an embedding, newest first."""
    cur.execute(f"""
        SELECT jp.job_id, r.role_name, COALESCE(jp.description_text, '')
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        WHERE {ELIGIBLE_WHERE} AND jp.domain IS NOT NULL AND jp.company_id = %s
        ORDER BY jp.ingested_at DESC
    """, (company_id,))
    return cur.fetchall()


def embed_company(conn, cur, model, company_id, batch_size):
    """Embed every missing job for one company. Returns the count written."""
    descriptions = fetch_company_descriptions(cur, company_id)
    boilerplate = find_boilerplate(descriptions) if descriptions else set()
    rows = fetch_company_jobs(cur, company_id)
    if not rows:
        return 0
    texts = [build_text(title, desc, boilerplate) for (_, title, desc) in rows]
    written = 0
    for chunk in batched(list(zip(rows, texts)), batch_size):
        chunk_rows = [r for r, _ in chunk]
        chunk_texts = [t for _, t in chunk]
        embeddings = model.encode(
            chunk_texts, batch_size=batch_size,
            show_progress_bar=False, convert_to_numpy=True,
        )
        updates = [
            (emb.tolist(), job_id)
            for (job_id, _, _), emb in zip(chunk_rows, embeddings)
        ]
        execute_batch(
            cur,
            "UPDATE job_postings SET embedding = %s WHERE job_id = %s",
            updates, page_size=batch_size,
        )
        conn.commit()
        written += len(updates)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                    help="Encode micro-batch size (default: %(default)s)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after embedding at most this many jobs")
    ap.add_argument("--company-limit", type=int, default=None,
                    help="Process at most this many companies (largest backlog first)")
    args = ap.parse_args(argv)

    print(f"Loading model {MODEL_NAME}...", flush=True)
    t0 = time.time()
    model = load_model()
    print(f"Model loaded in {time.time()-t0:.1f}s", flush=True)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            missing = count_missing(cur)
            stranded = count_stranded_domain_null(cur)
        print(f"Jobs needing embeddings: {missing:,}", flush=True)
        if stranded:
            print(f"WARNING: {stranded:,} tier-1 rows have domain IS NULL and "
                  "cannot embed until enrichment classifies them "
                  "(run enrich_job_postings --only-missing).", flush=True)

        if missing == 0:
            print("Nothing to do.", flush=True)
            return

        with conn.cursor() as cur:
            company_ids = companies_needing_embeddings(cur, args.company_limit)
        print(f"Companies to process: {len(company_ids):,} "
              f"(streamed one at a time; peak memory = largest company)", flush=True)

        processed = 0
        start = time.time()
        for company_id in company_ids:
            if args.limit is not None and processed >= args.limit:
                break
            with conn.cursor() as cur:
                written = embed_company(conn, cur, model, company_id, args.batch_size)
            processed += written
            if processed and processed % (args.batch_size * 10) < args.batch_size:
                elapsed = time.time() - start
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (missing - processed) / rate if rate > 0 else 0
                print(f"  {processed:,}/{missing:,} "
                      f"({100*processed/missing:.1f}%) - {rate:.1f} jobs/sec "
                      f"- ETA {eta/60:.1f} min", flush=True)

        print(f"\nDone. Embedded {processed:,} jobs in "
              f"{(time.time()-start)/60:.1f} min", flush=True)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
