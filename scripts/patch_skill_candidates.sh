#!/usr/bin/env bash
set -e

FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.skillcand_$ts"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# --- 1) Ensure we have needed imports (keep it minimal) ---
# We already have re, typing, etc. We'll add string + datetime if missing.
if not re.search(r'(?m)^import\s+string\s*$', s):
    s = re.sub(r'(?m)^(import\s+re\s*)$', r'\1\nimport string', s, count=1)

if not re.search(r'(?m)^from\s+datetime\s+import\s+datetime\s*$', s):
    # insert after existing datetime imports if any; else after typing import line
    if "from datetime import date" in s:
        s = s.replace("from datetime import date", "from datetime import date\nfrom datetime import datetime")
    else:
        # best effort: place after other imports early
        s = re.sub(r'(?m)^(from typing[^\n]*\n)', r'\1from datetime import datetime\n', s, count=1)

# --- 2) Insert candidate extraction helpers (only if not present) ---
marker = "# -----------------------------\n# Skill extraction + priority classification\n# -----------------------------"
if marker not in s:
    raise SystemExit("Could not find skill section marker in enrich_job_postings.py")

if "def upsert_skill_candidate(" not in s:
    insert_block = r'''

# -----------------------------
# Skill candidates + soft skills capture (NEW)
# -----------------------------

_CANDIDATE_STOPWORDS = {
    "about", "the", "and", "or", "to", "of", "in", "for", "with", "on", "a", "an",
    "you", "we", "our", "your", "role", "position", "team", "work", "experience",
    "skills", "skill", "requirements", "qualifications", "preferred", "required",
    "responsibilities", "responsibility", "ability", "must", "nice", "have", "plus",
}

_SOFT_SKILL_HINTS = {
    "communication", "communicator", "collaboration", "collaborate", "leadership",
    "organized", "organization", "detail oriented", "detail-oriented", "problem solving",
    "problem-solving", "time management", "stakeholder", "presentation", "presenting",
    "critical thinking", "adaptable", "proactive", "self motivated", "self-motivated",
    "ownership", "follow through", "follow-through",
}

def _norm_candidate(t: str) -> str:
    t = (t or "").strip().lower()
    t = t.replace("\u00a0", " ")
    t = re.sub(r"\s+", " ", t)
    # strip surrounding punctuation
    t = t.strip(string.punctuation + " ")
    # collapse internal punctuation spacing
    t = re.sub(r"\s*[/|]\s*", "/", t)
    return t

def _is_garbage_candidate(t: str) -> bool:
    if not t:
        return True
    if len(t) < 2 or len(t) > 60:
        return True
    if t in _CANDIDATE_STOPWORDS:
        return True
    if re.fullmatch(r"[\d\W_]+", t):
        return True
    # avoid "1 week ago", "over 100 applicants", etc.
    if re.search(r"\b(applicants|reposted|promoted|easy apply|show more|share)\b", t):
        return True
    # avoid obvious salary fragments
    if "$" in t or re.search(r"\b\d+k\b|\b\d+\s*/\s*(yr|hr|mo)\b", t):
        return True
    return False

def _split_skillish_tokens(line: str):
    """
    Split a line into likely skill tokens.
    Handles commas, bullets, parens, and common separators.
    """
    x = clean_text(line)
    # remove leading bullets
    x = re.sub(r"^\s*[-•\u2022]+\s*", "", x)
    # split on strong separators
    parts = re.split(r"[,\u2022;]|(?:\s+-\s+)|(?:\s+\|\s+)", x)
    out = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # further split on "and" when it looks like a list
        subparts = re.split(r"\s+\band\b\s+", part, flags=re.IGNORECASE)
        for sp in subparts:
            sp = sp.strip()
            if sp:
                out.append(sp)
    return out

def _extract_candidate_phrases_from_line(line: str):
    """
    Candidate phrases: acronyms (2-10 chars) + short phrases (1-4 words).
    """
    cands = []

    for token in _split_skillish_tokens(line):
        t = token.strip()
        if not t:
            continue

        # Pull out parenthetical variants: "Google Analytics (GA4)" => GA4 separately
        paren = re.findall(r"\(([^)]+)\)", t)
        for ptxt in paren:
            cands.append(ptxt.strip())

        t = re.sub(r"\([^)]*\)", "", t).strip()

        # If token contains slashes like "SQL/Python", split into both too
        if "/" in t and len(t) <= 40:
            for piece in t.split("/"):
                piece = piece.strip()
                if piece:
                    cands.append(piece)

        # Keep the main token too
        cands.append(t)

    # Normalize and filter
    cleaned = []
    for c in cands:
        n = _norm_candidate(c)
        if _is_garbage_candidate(n):
            continue
        # limit word count (avoid full sentences)
        wc = len(n.split())
        if wc > 4:
            continue
        cleaned.append((c.strip(), n))
    return cleaned

def _looks_like_soft_skill(norm: str) -> bool:
    # direct match on hint phrases
    if norm in _SOFT_SKILL_HINTS:
        return True
    for h in _SOFT_SKILL_HINTS:
        if h in norm:
            return True
    # pattern-y soft skill
    if re.search(r"\b(communicat|collabor|lead|organize|detail|stakeholder|present|priorit)\w*\b", norm):
        return True
    return False

def upsert_skill_candidate(cur, raw_text: str, normalized_text: str, skill_type_guess: str,
                          confidence: float, sample_job_id: str):
    """
    Upsert into skill_candidates(normalized_text unique)
    - increments seen_count
    - keeps a representative raw_text (prefer longer)
    - stores sample_job_id if empty
    """
    cur.execute(
        """
        INSERT INTO skill_candidates
            (raw_text, normalized_text, skill_type_guess, confidence, seen_count,
             first_seen_at, last_seen_at, sample_job_id, status)
        VALUES
            (%s, %s, %s, %s, 1, NOW(), NOW(), %s, 'new')
        ON CONFLICT (normalized_text)
        DO UPDATE SET
            seen_count = skill_candidates.seen_count + 1,
            last_seen_at = NOW(),
            confidence = GREATEST(skill_candidates.confidence, EXCLUDED.confidence),
            raw_text = CASE
                        WHEN length(EXCLUDED.raw_text) > length(skill_candidates.raw_text)
                        THEN EXCLUDED.raw_text
                        ELSE skill_candidates.raw_text
                      END,
            sample_job_id = COALESCE(skill_candidates.sample_job_id, EXCLUDED.sample_job_id)
        """,
        (raw_text, normalized_text, skill_type_guess, confidence, sample_job_id),
    )

def insert_soft_skill(cur, job_id: str, soft_skill: str, normalized_text: str, confidence: float, src: str = "regex"):
    cur.execute(
        """
        INSERT INTO job_soft_skills (job_id, soft_skill, normalized_text, confidence, extraction_src, created_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (job_id, normalized_text) DO NOTHING
        """,
        (job_id, soft_skill, normalized_text, confidence, src),
    )

def record_skill_candidates(cur, job_id: str, desc: str, compiled_patterns):
    """
    Extract candidate skills from likely sections/lines and upsert into skill_candidates.
    Avoid candidates that already match canonical alias patterns.
    Also writes soft skills into job_soft_skills.
    """
    # build ignore set from aliases that actually match this description
    ignore = set()
    t_all = normalize_for_matching(desc)
    for pat, _sid, alias in compiled_patterns:
        if pat.search(t_all):
            ignore.add(_norm_candidate(alias))

    lines = [clean_text(x) for x in desc.splitlines()]
    lines = [x for x in lines if x]

    # Focus on likely skill-heavy lines
    skillish_lines = []
    for ln in lines[:200]:  # cap to avoid insane scans
        low = normalize_for_matching(ln)
        if any(k in low for k in ["requirements", "qualifications", "skills", "preferred", "must have", "nice to have"]):
            skillish_lines.append(ln)
            continue
        if ln.strip().startswith(("-", "•")):
            skillish_lines.append(ln)
            continue

    for ln in skillish_lines:
        # derive a light context confidence
        pri = detect_priority_from_line(ln) or infer_section_priority(ln) or "required"
        base_conf = 0.80 if pri == "required" else (0.60 if pri == "preferred" else 0.45)

        for raw, norm in _extract_candidate_phrases_from_line(ln):
            if norm in ignore:
                continue

            is_soft = _looks_like_soft_skill(norm)
            skill_type_guess = "soft" if is_soft else "hard"

            # simple tweak: acronyms are often genuine hard skills/tools
            if re.fullmatch(r"[a-z]{2,10}\d{0,2}", norm) and not is_soft:
                conf = min(0.95, base_conf + 0.10)
            else:
                conf = base_conf

            upsert_skill_candidate(cur, raw_text=raw, normalized_text=norm,
                                  skill_type_guess=skill_type_guess, confidence=float(conf),
                                  sample_job_id=job_id)

            if is_soft:
                insert_soft_skill(cur, job_id, soft_skill=raw, normalized_text=norm,
                                  confidence=float(conf), src="regex")
'''
    # Insert right after the marker block header line (keeps file organized)
    s = s.replace(marker, marker + insert_block)

# --- 3) Wire it into the main loop (only if not already wired) ---
# Find the line where skill_priority_map is created, then insert call right after.
hook = "skill_priority_map = extract_skill_priorities(desc, compiled_patterns)"
if hook not in s:
    raise SystemExit("Could not find skill_priority_map assignment in main loop.")

if "record_skill_candidates(cur, job_id, desc, compiled_patterns)" not in s:
    s = s.replace(
        hook,
        hook + "\n                # NEW: capture candidate skills + soft skills\n                record_skill_candidates(cur, job_id, desc, compiled_patterns)"
    )

p.write_text(s, encoding="utf-8")
print("✅ Added skill candidate + soft skill capture to enrich_job_postings.py")
PY

python -m py_compile python/enrich_job_postings.py
echo "✅ Patch applied and file compiles."
echo "Backup: python/enrich_job_postings.py.bak.skillcand_$ts"
