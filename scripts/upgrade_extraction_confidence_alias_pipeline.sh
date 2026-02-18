#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"

FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.upgradeB_${ts}"
echo "🧾 Backup: ${FILE}.bak.upgradeB_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# -----------------------------
# 1) Patch resume_to_skills (B) block: bulk variant mapping
# -----------------------------
# We replace everything between:
#   "# (B) skills section parsing (great for resumes)"
# and
#   "# (C) Optional heuristic toolish tokens (placeholder)"
b_start = s.find("# (B) skills section parsing (great for resumes)")
c_start = s.find("# (C) Optional heuristic toolish tokens (placeholder)")

if b_start == -1 or c_start == -1 or c_start <= b_start:
    raise SystemExit("❌ Could not locate (B)/(C) anchors in resume_to_skills().")

block_b = s[b_start:c_start]

# If already upgraded, skip
if "token_variants" in block_b and "variant_set" in block_b and "alias_map" in block_b:
    print("ℹ️ resume_to_skills (B) already upgraded; skipping.")
else:
    # infer indentation from the line right after the header
    # find the line containing "section_lines"
    m_ind = re.search(r"(?m)^(?P<ind>\s*)section_lines\s*=\s*extract_skill_sections", block_b)
    if not m_ind:
        raise SystemExit("❌ Could not infer indent inside (B) block (missing section_lines=...).")
    ind = m_ind.group("ind")

    new_b = f"""{ind}# (B) skills section parsing (great for resumes)
{ind}section_lines = extract_skill_sections(resume_text)

{ind}# Tokenize section lines -> candidate tokens
{ind}section_tokens = []
{ind}for ln in section_lines:
{ind}    for tok in split_skill_list_line(ln):
{ind}        t = clean_text(tok)
{ind}        if t:
{ind}            section_tokens.append(t)

{ind}# Build token variants to improve recall (punctuation/space-insensitive)
{ind}def _variants(x: str):
{ind}    t = normalize_for_matching(x)
{ind}    out = set()
{ind}    out.add(t)
{ind}    out.add(re.sub(r"\\s+", " ", t).strip())
{ind}    out.add(re.sub(r"[^a-z0-9]+", "", t))                 # "power bi" -> "powerbi"
{ind}    out.add(re.sub(r"[^a-z0-9]+", " ", t).strip())        # "a/b" -> "a b"
{ind}    # common MS patterns
{ind}    out.add(t.replace("microsoft ", "ms "))
{ind}    out.add(t.replace("ms ", "microsoft "))
{ind}    # drop trailing periods (e.g. "sr." aliases if you add later)
{ind}    out.add(t.replace(".", ""))
{ind}    return [v for v in out if v]

{ind}token_variants = {{}}
{ind}variant_set = set()

{ind}for tok in section_tokens:
{ind}    vars_ = _variants(tok)
{ind}    # unique while preserving order
{ind}    seen = set()
{ind}    vars2 = []
{ind}    for v in vars_:
{ind}        if v not in seen:
{ind}            seen.add(v)
{ind}            vars2.append(v)
{ind}    token_variants[tok] = vars2
{ind}    for v in vars2:
{ind}        if len(v) >= 2:
{ind}            variant_set.add(v)

{ind}# Bulk map variants -> skill_id in one query
{ind}alias_map = {{}}
{ind}if variant_set:
{ind}    cur.execute(\"\"\"
{ind}      SELECT lower(alias_text) AS a, skill_id
{ind}      FROM skill_aliases
{ind}      WHERE lower(alias_text) = ANY(%s)
{ind}    \"\"\", (list(variant_set),))
{ind}    for r in cur.fetchall():
{ind}        alias_map[r["a"]] = r["skill_id"]

{ind}# Apply mapping (prefer earlier/better variants)
{ind}for tok in section_tokens:
{ind}    sid = None
{ind}    for v in token_variants.get(tok, []):
{ind}        sid = alias_map.get(v)
{ind}        if sid:
{ind}            break
{ind}    if sid:
{ind}        conf = 0.88
{ind}        evidence = tok
{ind}        prev = found.get(sid, (0, "", ""))
{ind}        if conf > prev[0]:
{ind}            found[sid] = (conf, evidence, "section")

"""

    s = s[:b_start] + new_b + s[c_start:]
    print("✅ Patched resume_to_skills(): (B) block upgraded to bulk variant mapping.")


# -----------------------------
# 2) Patch compute_confidence_score to incorporate source quality
# -----------------------------
# We will:
# - extend signature to accept skill_source_counts: Optional[dict]=None
# - penalize if most skills came from heuristic (lower trust)
# - reduce penalty if strong alias/section presence
#
# Then update the callsite in score_resume().

# Patch function signature + body hook
m_conf = re.search(r"(?s)def\s+compute_confidence_score\((.*?)\):\n(.*?)\n\n", s)
if not m_conf:
    raise SystemExit("❌ Could not locate compute_confidence_score()")

conf_block = s[m_conf.start():m_conf.end()]

if "skill_source_counts" in conf_block:
    print("ℹ️ compute_confidence_score already accepts skill_source_counts; skipping signature patch.")
else:
    # Replace signature line only
    conf_block2 = re.sub(
        r"def\s+compute_confidence_score\(([^)]*)\):",
        r"def compute_confidence_score(\1, skill_source_counts: Optional[dict] = None):",
        conf_block,
        count=1
    )

    # Insert logic near the top of function body after score=100 initialization
    hook = "\n    # Source-quality adjustment: alias/section > heuristic\n"
    hook += "    ssc = (skill_source_counts or {})\n"
    hook += "    n_alias = int(ssc.get('alias', 0) or 0)\n"
    hook += "    n_section = int(ssc.get('section', 0) or 0)\n"
    hook += "    n_heur = int(ssc.get('heuristic', 0) or 0)\n"
    hook += "    n_total = max(1, n_alias + n_section + n_heur)\n"
    hook += "    frac_heur = n_heur / float(n_total)\n"
    hook += "    frac_good = (n_alias + n_section) / float(n_total)\n"
    hook += "    if frac_heur >= 0.60:\n"
    hook += "        score -= 10\n"
    hook += "        flags.append('skills_mostly_heuristic')\n"
    hook += "    elif frac_good >= 0.60:\n"
    hook += "        score += 3  # small bump; cap below will apply\n"
    hook += "        flags.append('skills_mostly_high_confidence_sources')\n"

    # place hook after "score = 100"
    conf_block2 = conf_block2.replace("    score = 100\n", "    score = 100\n" + hook, 1)

    s = s[:m_conf.start()] + conf_block2 + s[m_conf.end():]
    print("✅ Patched compute_confidence_score(): added skill_source_counts source-quality logic.")

# Patch callsite: compute counts from resume_skills.source
# Find the call in score_resume()
call_pat = re.compile(r"conf_score,\s*conf_flags\s*=\s*compute_confidence_score\(\s*([\s\S]*?)\)", re.M)
m_call = call_pat.search(s)
if not m_call:
    raise SystemExit("❌ Could not locate compute_confidence_score(...) callsite in score_resume().")

call_text = m_call.group(0)
if "skill_source_counts" in call_text:
    print("ℹ️ compute_confidence_score callsite already passes skill_source_counts; skipping.")
else:
    # Insert before call: query source counts
    insert_anchor = m_call.start()
    # infer indentation from the call line
    line_start = s.rfind("\n", 0, insert_anchor) + 1
    ind2 = re.match(r"\s*", s[line_start:insert_anchor]).group(0)

    pre = (
        f"{ind2}# source breakdown of extracted skills (alias/section/heuristic)\n"
        f"{ind2}cur.execute(\"\"\"\n"
        f"{ind2}  SELECT source, COUNT(*) AS n\n"
        f"{ind2}  FROM resume_skills\n"
        f"{ind2}  WHERE resume_id=%s\n"
        f"{ind2}  GROUP BY source\n"
        f"{ind2}\"\"\", (resume_id,))\n"
        f"{ind2}skill_source_counts = {{r['source']: int(r['n']) for r in cur.fetchall()}}\n\n"
    )

    # Now inject skill_source_counts=skill_source_counts into compute_confidence_score(...)
    # We'll add as last argument inside the call.
    # Find the closing ")" of the call and inject before it (safe because it's a single call block here).
    # We patch by adding a new line "skill_source_counts=skill_source_counts"
    call_text2 = call_text
    # add before the final ")" inside the call
    call_text2 = call_text2.rstrip()
    # Ensure trailing comma before the new kwarg
    if call_text2.endswith(")"):
        call_text2 = call_text2[:-1]
        # If last non-space char before was not comma, add one
        if not call_text2.rstrip().endswith(","):
            call_text2 = call_text2.rstrip() + ",\n"
        call_text2 += f"{ind2}    skill_source_counts=skill_source_counts\n{ind2})"
    else:
        raise SystemExit("❌ Unexpected compute_confidence_score call formatting.")

    s = s[:insert_anchor] + pre + s[insert_anchor:]
    # Now replace the old call (first occurrence after insertion)
    s = s.replace(call_text, call_text2, 1)
    print("✅ Patched score_resume(): passes skill_source_counts into compute_confidence_score().")


# -----------------------------
# 3) Add alias backfill tool (idempotent)
# -----------------------------
import os
tool_path = Path("scripts/backfill_skill_alias_variants.py")
if not tool_path.exists():
    tool_path.write_text(
        """#!/usr/bin/env python3
import re
import os
import psycopg2
from psycopg2.extras import DictCursor

DB = os.getenv("PGDATABASE", "job_analytics")

def variants(a: str):
    a = (a or "").strip().lower()
    out = set()
    if not a:
        return out
    out.add(a)
    out.add(re.sub(r"\\s+", " ", a).strip())
    out.add(re.sub(r"[^a-z0-9]+", " ", a).strip())
    out.add(re.sub(r"[^a-z0-9]+", "", a))
    out.add(a.replace("microsoft ", "ms "))
    out.add(a.replace("ms ", "microsoft "))
    out.add(a.replace(".", ""))
    # avoid garbage
    out2 = set()
    for v in out:
        v = v.strip()
        if not v:
            continue
        if len(v) == 1 and v not in {"r","c"}:
            continue
        out2.add(v)
    return out2

def main(dry_run=True, limit=None):
    conn = psycopg2.connect(dbname=DB)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(\"\"\"
              SELECT skill_id, alias_text
              FROM skill_aliases
              WHERE alias_text IS NOT NULL AND btrim(alias_text) <> ''
            \"\"\")
            rows = cur.fetchall()

            to_add = []
            for r in rows:
                sid = r["skill_id"]
                a = r["alias_text"]
                for v in variants(a):
                    if v == a.strip().lower():
                        continue
                    to_add.append((sid, v))

            # de-dup
            to_add = list(dict.fromkeys(to_add))
            if limit:
                to_add = to_add[:limit]

            if not to_add:
                print("No variants to add.")
                return

            # filter out existing
            cur.execute(\"\"\"
              SELECT lower(alias_text) AS a, skill_id
              FROM skill_aliases
            \"\"\")
            existing = {(rr["skill_id"], rr["a"]) for rr in cur.fetchall()}

            final = [(sid, a) for (sid, a) in to_add if (sid, a) not in existing]

            print(f"Planned inserts: {len(final)} (out of {len(to_add)} candidates)")

            if dry_run:
                print("Dry-run: not inserting. Re-run with --apply to write.")
                return

            for sid, a in final:
                cur.execute(\"\"\"
                  INSERT INTO skill_aliases (skill_id, alias_text)
                  VALUES (%s, %s)
                  ON CONFLICT DO NOTHING
                \"\"\", (sid, a))

            conn.commit()
            print(f"Inserted: {len(final)}")
    finally:
        conn.close()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually insert rows")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    main(dry_run=(not args.apply), limit=args.limit)
""",
        encoding="utf-8"
    )
    os.chmod(tool_path, 0o755)
    print("✅ Added scripts/backfill_skill_alias_variants.py")
else:
    print("ℹ️ scripts/backfill_skill_alias_variants.py already exists; leaving as-is.")

p.write_text(s, encoding="utf-8")
print("✅ Upgrade B patch applied to score_resume.py")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"

echo ""
echo "Next steps:"
echo "  1) Re-score: ./scripts/score_resume.sh ~/Downloads/resume.pdf"
echo "  2) Optional alias growth (dry-run): python scripts/backfill_skill_alias_variants.py"
echo "  3) Optional alias growth (apply):   python scripts/backfill_skill_alias_variants.py --apply"
