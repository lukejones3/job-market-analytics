#!/usr/bin/env python3
"""
Train a 3+1 class experience-level classifier.

Classes trained: junior / senior / lead  (title-certain labels only)
Mid is NOT trained — it is inferred at inference time when the model is
uncertain between junior and senior and the title has no level keyword.
This is intentional: no clean title-certain signal exists for mid.

Embedding strategy: uses stored embeddings from job_postings.embedding
(already computed by embed_jobs.py). model.encode() is only called in
apply_predictions() for the rare jobs that lack a stored embedding.

Output artifact: /opt/job-market-analytics/models/exp_level_classifier.pkl
Write target:    job_postings.experience_level_v2 (parallel to existing column)
Existing column: experience_level — UNTOUCHED by this script.

To retrain:
    /opt/job-market-analytics/.venv/bin/python \\
        /opt/job-market-analytics/python/train_exp_level_classifier.py

Add --apply to write predictions to experience_level_v2 after training.
"""

import argparse
import csv
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_utils import compute_boilerplate_sets, build_text_for_classifier

load_dotenv(Path(__file__).parent.parent / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
ARTIFACT_PATH   = Path('/opt/job-market-analytics/models/exp_level_classifier.pkl')
SPOTCHECK_CSV   = Path('/opt/job-market-analytics/models/exp_level_spotcheck_200.csv')
DISAGREE_CSV    = Path('/opt/job-market-analytics/models/exp_level_disagreements_200.csv')

# ── Model config ────────────────────────────────────────────────────────────
MODEL_NAME  = 'all-MiniLM-L6-v2'
DESC_CHARS  = 1500
BATCH_SIZE  = 64

# ── Mid-zone inference thresholds ───────────────────────────────────────────
MID_CONF_LOW  = 0.40
MID_CONF_HIGH = 0.65

# ── Regex patterns (Python equivalents of the SQL \m...\M title queries) ───
# Priority for label assignment: junior > lead > senior
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


def label_from_title(title: str):
    """Return title-certain label (junior/lead/senior) or None if ambiguous."""
    if JUNIOR_RE.search(title or ''):
        return 'junior'
    if LEAD_RE.search(title or ''):
        return 'lead'
    if SENIOR_RE.search(title or ''):
        return 'senior'
    return None


def predict_with_mid_zone(clf, embeddings: np.ndarray, titles: list[str]) -> list[str]:
    """Apply full inference logic: title rules → ML → mid-zone.

    1. Title-first: JUNIOR_RE matches → junior (ML not consulted).
    2. ML classifier runs for all other titles.
    3. Title safety override: ML predicts junior but SENIOR_RE/LEAD_RE matches → use title match.
    4. Mid-zone: top class in (junior, senior), conf in [MID_CONF_LOW, MID_CONF_HIGH],
       no level keyword in title → 'mid'.
    5. Low confidence (<0.50, no level keyword) → 'mid'. No LLM fallback.
    6. Otherwise: ML prediction.
    """
    probas = clf.predict_proba(embeddings)
    classes = list(clf.classes_)
    results = []
    for i, title in enumerate(titles):
        t = title or ''

        # 1. Title-first junior rule
        if JUNIOR_RE.search(t):
            results.append('junior')
            continue

        top_idx = int(np.argmax(probas[i]))
        top_class = classes[top_idx]
        top_conf = float(probas[i, top_idx])

        # 2. Title safety override: ML junior prediction on senior/lead title
        if top_class == 'junior':
            if LEAD_RE.search(t):
                results.append('lead')
                continue
            if SENIOR_RE.search(t):
                results.append('senior')
                continue

        has_level_kw = bool(LEVEL_KW_RE.search(t))

        # 3. Mid-zone heuristic + low-confidence catch-all (no LLM)
        if (top_class in ('junior', 'senior')
                and MID_CONF_LOW <= top_conf <= MID_CONF_HIGH
                and not has_level_kw):
            results.append('mid')
        elif top_conf < 0.50 and not has_level_kw:
            results.append('mid')
        else:
            results.append(top_class)
    return results


def load_training_data(conn):
    """Return title-certain examples using stored embeddings from job_postings.embedding."""
    log.info("Querying title-certain training examples (stored embeddings)...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT jp.job_id, r.role_name,
                   COALESCE(jp.description_text, ''), jp.experience_level,
                   jp.embedding
            FROM job_postings jp
            JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.status='raw' AND jp.data_tier=1 AND jp.domain IS NOT NULL
              AND jp.embedding IS NOT NULL
        """)
        rows = cur.fetchall()

    embeddings, labels, descs, job_ids, titles, old_labels = [], [], [], [], [], []
    skipped_ambiguous = 0
    for job_id, role_name, desc, exp_level, emb_str in rows:
        lbl = label_from_title(role_name)
        if lbl is None:
            skipped_ambiguous += 1
            continue
        embeddings.append(parse_embedding(emb_str))
        labels.append(lbl)
        descs.append(desc)
        job_ids.append(job_id)
        titles.append(role_name)
        old_labels.append(exp_level)

    dist = Counter(labels)
    log.info(f"Training set: {len(labels):,} title-certain examples "
             f"({skipped_ambiguous:,} ambiguous skipped; embedding IS NOT NULL filter applied)")
    log.info(f"Class distribution: {dict(dist)}")
    X = np.array(embeddings, dtype=np.float32)
    return descs, labels, X, job_ids, titles, old_labels, dist


def write_spotcheck_csv(job_ids, titles, labels, descs, n=200):
    rng = np.random.default_rng(42)
    idx = rng.choice(len(labels), size=min(n, len(labels)), replace=False)
    with open(SPOTCHECK_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['job_id', 'title', 'assigned_label', 'desc_snippet_200chars'])
        for i in idx:
            snippet = (titles[i] + ' ' + descs[i])[:200]
            writer.writerow([job_ids[i], titles[i], labels[i], snippet])
    log.info(f"Spot-check CSV ({n} rows): {SPOTCHECK_CSV}")


def print_validation_report(y_val, y_pred, classes):
    print("\n" + "=" * 65)
    print("VALIDATION — per-class precision / recall / F1")
    print("=" * 65)
    report = classification_report(y_val, y_pred, labels=classes, digits=3)
    print(report)

    from sklearn.metrics import precision_recall_fscore_support
    p, r, f, _ = precision_recall_fscore_support(
        y_val, y_pred, labels=['junior'], average=None, zero_division=0
    )
    print(f"  >>> JUNIOR  precision={p[0]:.3f}  recall={r[0]:.3f}  F1={f[0]:.3f}")

    print()
    print("CONFUSION MATRIX  (rows=actual, cols=predicted)")
    cm = confusion_matrix(y_val, y_pred, labels=classes)
    header = f"{'':>12}" + "".join(f"{c:>10}" for c in classes)
    print(header)
    for i, actual in enumerate(classes):
        row = f"{actual:>12}" + "".join(f"{cm[i, j]:>10}" for j in range(len(classes)))
        print(row)
    print()

    junior_idx = classes.index('junior') if 'junior' in classes else None
    if junior_idx is not None:
        junior_row = cm[junior_idx]
        print("Junior misclassified as:")
        for j, c in enumerate(classes):
            if j != junior_idx and junior_row[j] > 0:
                print(f"  → {c}: {junior_row[j]} ({100*junior_row[j]/max(1,junior_row.sum()):.1f}%)")
    print("=" * 65 + "\n")


def estimate_fallback_rate(conn, clf, n_sample=1000):
    """Estimate mid-zone rate using stored embeddings of mid-labelled jobs."""
    log.info(f"Estimating mid-zone rate on {n_sample} mid-labelled jobs (stored embeddings)...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.role_name, jp.embedding
            FROM job_postings jp
            JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.status='raw' AND jp.data_tier=1 AND jp.domain IS NOT NULL
              AND jp.experience_level = 'mid' AND jp.embedding IS NOT NULL
            ORDER BY RANDOM()
            LIMIT %s
        """, (n_sample,))
        rows = cur.fetchall()

    if not rows:
        log.warning("No mid-labelled jobs with stored embeddings found.")
        return

    X = np.array([parse_embedding(r[1]) for r in rows])
    titles_sample = [r[0] for r in rows]

    classes = list(clf.classes_)
    probas = clf.predict_proba(X)
    top_confs = probas.max(axis=1)
    top_preds = [classes[int(np.argmax(probas[i]))] for i in range(len(rows))]

    in_mid_zone = sum(
        1 for i, title in enumerate(titles_sample)
        if top_preds[i] in ('junior', 'senior')
        and MID_CONF_LOW <= top_confs[i] <= MID_CONF_HIGH
        and not LEVEL_KW_RE.search(title or '')
    )
    below_threshold = sum(1 for c in top_confs if c < 0.50)

    print("\n--- Mid-Zone Rate on Currently-Mid Jobs ---")
    print(f"Sample: {len(rows)} currently-mid-labelled jobs")
    print(f"Median classifier confidence: {np.median(top_confs):.3f}")
    print(f"Jobs in mid-zone [conf {MID_CONF_LOW}–{MID_CONF_HIGH}, no level kw]: "
          f"{in_mid_zone} ({100*in_mid_zone/len(rows):.1f}%)")
    print(f"Jobs below 0.50 confidence (→ mid catch-all, no LLM): "
          f"{below_threshold} ({100*below_threshold/len(rows):.1f}%)")
    print()


def build_disagreement_sample(conn, clf, n_pool=2000):
    """Compare new classifier vs old experience_level using stored embeddings.

    Junior-predicted jobs are oversampled: up to 50 junior predictions are
    included first, then filled to 200 with other disagreements.
    """
    log.info(f"Building disagreement comparison on {n_pool}-job pool (stored embeddings)...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT jp.job_id, r.role_name, jp.experience_level, jp.embedding
            FROM job_postings jp
            JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.status='raw' AND jp.data_tier=1 AND jp.domain IS NOT NULL
              AND jp.experience_level IS NOT NULL AND jp.embedding IS NOT NULL
            ORDER BY RANDOM()
            LIMIT %s
        """, (n_pool,))
        rows = cur.fetchall()

    log.info(f"Loaded {len(rows):,} stored embeddings for disagreement pool...")
    X = np.array([parse_embedding(r[3]) for r in rows])

    probas = clf.predict_proba(X)
    classes = list(clf.classes_)
    raw_preds = [classes[int(np.argmax(probas[i]))] for i in range(len(rows))]
    top_confs = [float(probas[i].max()) for i in range(len(rows))]
    titles_list = [r[1] for r in rows]

    v2_preds = predict_with_mid_zone(clf, X, titles_list)

    OLD_TO_V2 = {'entry': 'junior', 'mid': 'mid', 'senior': 'senior', 'associate': 'junior'}

    junior_predictions = []
    disagreements = []
    for i, (job_id, role_name, old_label, _) in enumerate(rows):
        v2 = v2_preds[i]
        conf = top_confs[i]
        old_v2_equiv = OLD_TO_V2.get(old_label or '', None)

        is_disagree = old_v2_equiv is not None and v2 != old_v2_equiv
        rec = (job_id, role_name, v2, old_label, f"{conf:.3f}", is_disagree)

        if v2 == 'junior':
            junior_predictions.append(rec)
        if is_disagree:
            disagreements.append(rec)

    junior_sample = junior_predictions[:50]
    non_junior_disagree = [d for d in disagreements if d[2] != 'junior']
    fill_n = max(0, 200 - len(junior_sample))
    final_sample = junior_sample + non_junior_disagree[:fill_n]

    with open(DISAGREE_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'job_id', 'title', 'new_v2_prediction', 'old_experience_level',
            'confidence', 'is_disagreement',
        ])
        for rec in final_sample:
            writer.writerow(rec)

    print("\n--- Disagreement Sample ---")
    print(f"Pool size: {n_pool} labelled jobs")
    print(f"Total disagreements: {len(disagreements)} ({100*len(disagreements)/len(rows):.1f}%)")
    print(f"Jobs new classifier predicted 'junior': {len(junior_predictions)}")
    print(f"  → {len(junior_sample)} junior-predicted rows in CSV (oversampled)")
    print(f"  + {min(fill_n, len(non_junior_disagree))} other disagreements")
    print(f"CSV: {DISAGREE_CSV}")
    print()


def apply_predictions(conn, clf, batch_size=256):
    """Write experience_level_v2 predictions for all eligible jobs.

    Uses stored embeddings where available. For jobs without a stored embedding
    (new ingests not yet through embed_jobs.py), loads the sentence transformer
    and encodes on the fly using boilerplate-stripped text.

    Inference order: title_junior rule → ML → title_override → mid_zone.
    No LLM fallback — low-confidence predictions go to 'mid'.
    """
    log.info("Applying classifier predictions to experience_level_v2...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT jp.job_id, jp.company_id, r.role_name,
                   COALESCE(jp.description_text, ''), jp.embedding
            FROM job_postings jp
            JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.status='raw' AND jp.data_tier=1 AND jp.domain IS NOT NULL
              AND jp.experience_level_v2 IS NULL
            ORDER BY jp.ingested_at DESC
        """)
        rows = cur.fetchall()

    log.info(f"Jobs needing experience_level_v2: {len(rows):,}")
    if not rows:
        return

    # Split into stored-embedding vs needs-encoding groups
    stored = [(i, r) for i, r in enumerate(rows) if r[4] is not None]
    needs_encode = [(i, r) for i, r in enumerate(rows) if r[4] is None]
    log.info(f"  {len(stored):,} with stored embeddings, {len(needs_encode):,} need encoding")

    # Pre-allocate embedding matrix
    dim = len(parse_embedding(stored[0][1][4])) if stored else 384
    all_embeddings = np.zeros((len(rows), dim), dtype=np.float32)

    for i, r in stored:
        all_embeddings[i] = parse_embedding(r[4])

    if needs_encode:
        log.info("Loading sentence transformer for jobs without stored embeddings...")
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(MODEL_NAME)
        model.max_seq_length = 256

        log.info("Computing boilerplate sets for encoding...")
        from collections import defaultdict
        with conn.cursor() as cur:
            cur.execute("""
                SELECT jp.company_id, COALESCE(jp.description_text, '')
                FROM job_postings jp
                WHERE jp.status='raw' AND jp.data_tier=1 AND jp.domain IS NOT NULL
            """)
            all_desc_rows = cur.fetchall()
        company_descs = defaultdict(list)
        for cid, desc in all_desc_rows:
            company_descs[cid].append(desc)
        bp_by_company = compute_boilerplate_sets(company_descs)

        encode_batch_texts = [
            build_text_for_classifier(r[2], r[3], bp_by_company.get(r[1], set()), desc_chars=DESC_CHARS)
            for _, r in needs_encode
        ]
        encoded = model.encode(encode_batch_texts, batch_size=BATCH_SIZE,
                               show_progress_bar=True, convert_to_numpy=True)
        for batch_i, (orig_i, _) in enumerate(needs_encode):
            all_embeddings[orig_i] = encoded[batch_i]

    classes = list(clf.classes_)
    path_counts = Counter()
    processed = 0
    updates = []

    for start in range(0, len(rows), batch_size):
        batch_rows = rows[start:start + batch_size]
        batch_emb = all_embeddings[start:start + batch_size]

        probas = clf.predict_proba(batch_emb)
        top_confs = probas.max(axis=1)
        top_preds = [classes[int(np.argmax(probas[i]))] for i in range(len(batch_rows))]

        for i, (job_id, _, title, _, _) in enumerate(batch_rows):
            t = title or ''
            top_class = top_preds[i]
            top_conf = float(top_confs[i])

            if JUNIOR_RE.search(t):
                v2_pred = 'junior'
                path = 'title_junior'
            elif top_class == 'junior' and LEAD_RE.search(t):
                v2_pred = 'lead'
                path = 'title_override'
            elif top_class == 'junior' and SENIOR_RE.search(t):
                v2_pred = 'senior'
                path = 'title_override'
            else:
                has_level_kw = bool(LEVEL_KW_RE.search(t))
                if (top_class in ('junior', 'senior')
                        and MID_CONF_LOW <= top_conf <= MID_CONF_HIGH
                        and not has_level_kw):
                    v2_pred = 'mid'
                    path = 'mid_zone'
                elif top_conf < 0.50 and not has_level_kw:
                    v2_pred = 'mid'
                    path = 'mid_zone'
                else:
                    v2_pred = top_class
                    path = 'classifier'

            path_counts[path] += 1
            updates.append((v2_pred, job_id))

        if len(updates) >= batch_size * 4:
            with conn.cursor() as cur:
                execute_batch(
                    cur,
                    "UPDATE job_postings SET experience_level_v2 = %s WHERE job_id = %s",
                    updates, page_size=batch_size,
                )
            conn.commit()
            processed += len(updates)
            log.info(f"  Written {processed:,}/{len(rows):,}...")
            updates = []

    if updates:
        with conn.cursor() as cur:
            execute_batch(
                cur,
                "UPDATE job_postings SET experience_level_v2 = %s WHERE job_id = %s",
                updates, page_size=batch_size,
            )
        conn.commit()
        processed += len(updates)

    log.info(f"Done. experience_level_v2 written for {processed:,} jobs.")
    print("\n--- Prediction Path Distribution ---")
    total = sum(path_counts.values())
    for path, count in sorted(path_counts.items(), key=lambda x: -x[1]):
        print(f"  {path}: {count:,} ({100*count/total:.1f}%)")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='After training, write predictions to experience_level_v2')
    args = parser.parse_args()

    conn = get_conn()

    # ── Training data (stored embeddings — no model.encode() needed) ──────
    descs, labels, X, job_ids, titles, old_labels, dist = load_training_data(conn)
    log.info(f"Loaded embedding matrix: {X.shape}")

    write_spotcheck_csv(job_ids, titles, labels, descs, n=200)

    y = np.array(labels)

    # ── Train / val split ────────────────────────────────────────────────
    X_train, X_val, y_train, y_val, titles_train, titles_val = train_test_split(
        X, y, titles, test_size=0.20, random_state=42, stratify=y
    )
    log.info(f"Train: {len(X_train):,}  Val: {len(X_val):,}")

    # ── Train ────────────────────────────────────────────────────────────
    CLASS_WEIGHT = {'junior': 4, 'senior': 1, 'lead': 1}
    log.info(f"Training LogisticRegression (class_weight={CLASS_WEIGHT}, C=1.0, max_iter=1000)...")
    t0 = time.time()
    clf = LogisticRegression(
        class_weight=CLASS_WEIGHT,
        max_iter=1000,
        random_state=42,
        C=1.0,
    )
    clf.fit(X_train, y_train)
    log.info(f"Trained in {time.time()-t0:.1f}s")

    classes = sorted(clf.classes_.tolist())

    # ── Validate: ML classifier alone ────────────────────────────────────
    y_pred_ml = clf.predict(X_val)
    print("\n[ML CLASSIFIER ONLY — 4x weights, no title rules]")
    print_validation_report(y_val, y_pred_ml, classes)

    # ── Validate: full system (title rules + ML + mid-zone) ──────────────
    y_pred_sys = predict_with_mid_zone(clf, X_val, list(titles_val))
    print("[FULL SYSTEM — title-first junior rule → ML → title override → mid-zone]")
    print_validation_report(y_val, y_pred_sys, classes)

    # Title override count on val set
    ml_junior_fps = [
        i for i in range(len(y_val))
        if y_pred_ml[i] == 'junior' and y_val[i] != 'junior'
    ]
    overrides = [
        i for i in ml_junior_fps
        if SENIOR_RE.search(titles_val[i] or '') or LEAD_RE.search(titles_val[i] or '')
    ]
    print(f"Title override summary (val set):")
    print(f"  ML junior false positives (non-junior actual): {len(ml_junior_fps)}")
    print(f"  → caught by SENIOR_RE/LEAD_RE title override:  {len(overrides)}")
    print(f"  → remaining after override:                    {len(ml_junior_fps) - len(overrides)}")
    print()

    y_pred = y_pred_sys

    # ── Save artifact ────────────────────────────────────────────────────
    from sklearn.metrics import classification_report as cr
    artifact = {
        'classifier': clf,
        'model_name': MODEL_NAME,
        'classes': classes,
        'class_weight': CLASS_WEIGHT,
        'desc_chars': DESC_CHARS,
        'mid_conf_low': MID_CONF_LOW,
        'mid_conf_high': MID_CONF_HIGH,
        'level_kw_pattern': LEVEL_KW_RE.pattern,
        'training_dist': dict(dist),
        'trained_at': datetime.utcnow().isoformat(),
        'val_report': cr(y_val, y_pred, labels=classes, digits=3, output_dict=True),
    }
    joblib.dump(artifact, ARTIFACT_PATH)
    log.info(f"Artifact saved: {ARTIFACT_PATH}")

    # ── Mid-zone rate estimate (stored embeddings) ────────────────────────
    estimate_fallback_rate(conn, clf, n_sample=1000)

    # ── Disagreement sample (stored embeddings) ───────────────────────────
    build_disagreement_sample(conn, clf, n_pool=2000)

    # ── Optional: apply predictions ──────────────────────────────────────
    if args.apply:
        apply_predictions(conn, clf)

    conn.close()
    log.info("Done.")


if __name__ == '__main__':
    main()
