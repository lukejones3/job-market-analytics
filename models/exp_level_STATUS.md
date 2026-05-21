# experience_level_v2 — Status & Handoff

**Last updated:** 2026-05-21
**Status:** ON HOLD after Phase 1 integration. Phase 2 (skill extraction) NOT started.

---

## What's deployed

**Artifact:** `/opt/job-market-analytics/models/exp_level_classifier.pkl`
**Write target:** `job_postings.experience_level_v2` (parallel column; `experience_level` untouched)
**Daily cron:** `55 6 * * *` → `classify_exp_level_v2.py --apply` (runs after embed at 06:45)
**Training script:** `python/train_exp_level_classifier.py` (retrain is manual, no cron)
**Inference script:** `python/classify_exp_level_v2.py`

---

## Classifier config

- **Model:** LogisticRegression on all-MiniLM-L6-v2 stored embeddings (384-dim, parsed from `job_postings.embedding`)
- **Classes trained:** junior / senior / lead (3-class; `mid` is NOT a trained class — inferred via heuristics)
- **Class weight:** `{'junior': 4, 'senior': 1, 'lead': 1}`
  - `balanced` (~13x) caused junior precision = 0.539 — too many false positives
  - 4x is the inflection point: precision = 0.812, recall = 0.722, F1 = 0.765
- **C:** 1.0, **max_iter:** 1000

---

## Inference design (title-first, no LLM)

```
1. JUNIOR_RE matches title          → 'junior'   [ML not consulted]
2. ML predicts 'junior' + LEAD_RE   → 'lead'     [title override]
3. ML predicts 'junior' + SENIOR_RE → 'senior'   [title override]
4. top_class in (junior, senior)
   AND conf in [0.40, 0.65]
   AND no LEVEL_KW in title         → 'mid'      [mid-zone]
5. conf < 0.50 AND no LEVEL_KW      → 'mid'      [low-conf catch-all]
6. else                             → ML top class
```

**Zero LLM calls.** Low confidence → `'mid'` directly.

---

## Validation metrics (title-certain val set, 4x weight)

Title-certain jobs (those with level keywords) have deterministic outcomes via the title rules:

| | Precision | Recall | F1 |
|---|---|---|---|
| junior | 1.000 | 1.000 | 1.000 |
| senior | — | — | — |
| lead | — | — | — |

- All 126 junior val examples had junior-certain titles → title rule fires perfectly
- ML produced 20 junior false positives on non-junior val examples → ALL 20 caught by SENIOR_RE/LEAD_RE title override
- **Zero junior false positives survive into full-system output**

ML-only (pre-title-rules) at 4x weight: junior precision = 0.812, recall = 0.722

---

## Backfill distribution (40,852 jobs, 2026-05-20)

| Class | Count | % |
|-------|-------|---|
| senior | 19,460 | 47.6% |
| lead | 17,015 | 41.7% |
| mid | 3,385 | 8.3% |
| junior | 992 | 2.4% |

**Path breakdown:** classifier 90.0% · mid_zone 8.3% · title_junior 1.5% · title_override 0.2%

---

## Known open problem: ambiguous-title IC roles

The ambiguous-title spot-check (`models/ambiguous_title_spotcheck_30.csv`) revealed that
individual-contributor roles with no level keyword — "Account Executive", "Financial Analyst",
"Business Analyst" — tend to predict senior or lead with moderate confidence (0.57–0.80).

**Root cause:** `mid` is not a trained class. The training set only has junior/senior/lead labels,
so the model has no representation of mid-level ICs. When it sees an IC role title + description
without strong seniority signal, it guesses from the three trained classes, and senior/lead dominate
by training volume (senior 11,538 / lead 12,837 vs junior 628).

### Candidate fixes (not yet evaluated)

1. **Widen the mid-zone band** — raise `MID_CONF_HIGH` from 0.65 → 0.75 or 0.80 for the
   `top_class == 'senior'` arm. Would pull more ambiguous senior predictions into mid without
   retraining. Quick to test; risk of overcorrecting on genuinely senior titles that happen to
   have moderate confidence.

2. **Add parsed years-of-experience as a feature** — many job descriptions explicitly state
   "3-5 years experience". Extract this as a scalar feature and concatenate with the embedding
   before the LR head (or train a separate shallow head). Would give the model a direct signal
   for mid-level. Requires feature engineering + retrain.

3. **Derive real mid training labels** — use `experience_level = 'mid'` from the existing column
   (or a curated SQL query: titles that are clearly IC + no seniority signal) to generate a
   genuine mid class. Retrain as 4-class. This is the cleanest fix but requires label curation
   work to avoid contaminating the mid class with ambiguous examples.

---

## Resume checklist

- [ ] Review ambiguous-title spot-check CSV; decide which candidate fix to pursue
- [ ] If widening mid-zone: update `MID_CONF_HIGH` in both `classify_exp_level_v2.py` and
      `train_exp_level_classifier.py`, re-run backfill dry-run, spot-check again
- [ ] If retraining (fixes 2 or 3): retrain manually, re-check validation metrics, then re-backfill
- [ ] Phase 2 (skill extraction) remains blocked until mid-class problem is resolved
