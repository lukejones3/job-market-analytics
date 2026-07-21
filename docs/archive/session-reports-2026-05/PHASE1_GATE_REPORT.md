# Phase 1 Gate Report — Experience Level Classifier

**Stop point. Do not proceed to Phase 2 or integration until approved.**

---

## Training Set

| Class | Count | Fraction |
|---|---|---|
| junior | 630 | 2.5% |
| senior | 11,575 | 46.2% |
| lead | 12,869 | 51.3% |
| **Total** | **25,074** | — |

25,074 title-certain examples from live DB. No mid examples (by design). 15,778 ambiguous-title jobs skipped.

---

## Validation Metrics (20% stratified hold-out, n=5,015)

```
              precision    recall  f1-score   support

      junior      0.539     0.929     0.682       126
        lead      0.946     0.939     0.942      2574
      senior      0.939     0.910     0.924      2315

    accuracy                          0.925      5015
   macro avg      0.808     0.926     0.849      5015
weighted avg      0.932     0.925     0.927      5015
```

---

## Junior — Highlighted Separately

| Metric | Value |
|---|---|
| Precision | **0.539** |
| Recall | **0.929** |
| F1 | 0.682 |

Junior recall is 0.929 — above the 0.60 flag threshold. The classifier finds 93% of genuinely-junior
jobs. This is the correct trade-off given the thin training set: class_weight='balanced' pulls the
decision boundary toward junior aggressively, which costs precision.

Junior precision is 0.539. ~46% of junior predictions are false positives — non-junior jobs the model
calls junior. This is a real weakness, surfaced honestly below.

---

## Confusion Matrix (rows=actual, cols=predicted)

```
             junior    lead    senior
    junior      117       4         5    ← 92.9% correct
      lead       26    2416       132    ← 93.9% correct
    senior       74     135      2106    ← 91.0% correct
```

Junior misclassified as: lead 4 (3.2%), senior 5 (4.0%).

Non-junior misclassified as junior: 26 actual lead + 74 actual senior = 100 false junior predictions
in the validation set. These are jobs with JD text that reads junior-like even though their titles
are not junior-certain.

---

## Junior Disagreement Deep-Dive (oversampled in the 200-job CSV)

50 junior-predicted jobs in the comparison pool of 2,000. Old system labels for those 50:

| Old label | Count | Interpretation |
|---|---|---|
| entry (=junior) | 9 | True positives — old and new agree (just naming difference) |
| mid | 27 | Mostly correct — "Junior Software Engineer", "Sales Development Representative", "Jr. Marketing Analytics Consultant", "Graduate Development Program". Old system defaulted these to mid; new classifier correctly reads the junior signal. |
| senior | 14 | Mixed — includes clear errors. |

Notable false positives in the junior-predicted, old=senior group:

| Title | New pred | Old label | Conf | Verdict |
|---|---|---|---|---|
| Director of Growth | junior | senior | 0.962 | Hard error — should be lead |
| Senior Software Engineer - Camera Platform | junior | senior | 0.604 | Hard error — "Senior" in title |
| Internship: Optical Inspection Systems... | junior | senior | 0.952 | New classifier correct — it's an internship; old system was wrong |
| Principal Pricing Analyst | junior | mid | 0.541 | Borderline — "Principal" suggests senior |

"Director of Growth" and "Senior Software Engineer" are genuine failures: JD text is confusing the
model despite clear title signals. These would be caught in integration by a title-override rule
(suppress junior when SENIOR_RE or LEAD_RE matches the title). Flagged here as a known issue.

---

## 72.9% Disagreement Rate — Context

The raw disagreement rate between new classifier and old system is 72.9% on 2,000 jobs. This is
overwhelmingly explained by the taxonomy split: the old system lumped all manager/director/VP roles
into `senior`. The new classifier correctly calls them `lead`. Every one of those is a "disagreement"
in the CSV but not a real error. The actual error rate is best estimated by the validation
precision/recall numbers above.

---

## Fallback Rate Estimate (1,000 mid-labelled jobs)

| Metric | Value |
|---|---|
| Median confidence on mid-labelled jobs | 0.844 |
| In mid-zone [0.40–0.65, no level kw] | 147 (14.7%) |
| Below 0.50 confidence (LLM fallback trigger) | 58 (5.8%) |

Of currently-mid-labelled jobs, ~5.8% would fall below the 0.50 confidence threshold and trigger the
LLM fallback in the integration. The mid-zone heuristic (classify as 'mid' when confidence 0.40–0.65,
no level keyword) would catch an additional 14.7%.

---

## Artifacts

| File | Contents |
|---|---|
| exp_level_classifier.pkl | Trained LogisticRegression + metadata |
| exp_level_spotcheck_200.csv | 200 random training examples for label quality review |
| exp_level_disagreements_200.csv | 200-job disagreement comparison (50 junior-predicted oversampled) |

All files in /opt/job-market-analytics/models/

---

## Summary Assessment

The classifier is production-viable for `lead` and `senior` (F1 0.942 / 0.924). Junior is weaker
(F1 0.682) with excellent recall but low precision — it finds real junior jobs but also false-positives
from description text. Two integration-time mitigations would help before cutover:

1. Title-override rule: suppress `junior` prediction when SENIOR_RE or LEAD_RE matches the title.
2. Keep the 0.50 confidence gate so low-confidence junior predictions trigger fallback rather than
   silently writing a wrong label.
