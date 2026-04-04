#!/usr/bin/env python3
"""
discover_skills.py

Finds skill candidates by analyzing co-occurrence patterns in job descriptions.
Only surfaces terms that appear alongside known skills in bullet points —
much cleaner signal than raw frequency counting.

Usage:
    python python/discover_skills.py              # show candidates
    python python/discover_skills.py --promote    # interactive promotion mode
    python python/discover_skills.py --limit 30   # show top 30
"""

import os
import re
import argparse
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

# Patterns that signal a skill mention
SKILL_SIGNAL_PATTERNS = [
    r"experience (?:with|in|using)\s+([A-Za-z][A-Za-z0-9\s\.\+\#\-]{1,30}?)(?:\s*[,\.\(\)]|$)",
    r"proficiency (?:with|in)\s+([A-Za-z][A-Za-z0-9\s\.\+\#\-]{1,30}?)(?:\s*[,\.\(\)]|$)",
    r"knowledge of\s+([A-Za-z][A-Za-z0-9\s\.\+\#\-]{1,30}?)(?:\s*[,\.\(\)]|$)",
    r"familiarity (?:with|in)\s+([A-Za-z][A-Za-z0-9\s\.\+\#\-]{1,30}?)(?:\s*[,\.\(\)]|$)",
    r"expertise (?:with|in)\s+([A-Za-z][A-Za-z0-9\s\.\+\#\-]{1,30}?)(?:\s*[,\.\(\)]|$)",
    r"working knowledge of\s+([A-Za-z][A-Za-z0-9\s\.\+\#\-]{1,30}?)(?:\s*[,\.\(\)]|$)",
]

# Terms to always exclude
EXCLUDE_TERMS = {
    "the", "and", "or", "with", "in", "a", "an", "to", "for", "of", "on",
    "at", "by", "from", "as", "is", "are", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "must", "shall", "can", "need", "dare", "ought", "used",
    "data", "team", "role", "years", "year", "experience", "work", "working",
    "strong", "excellent", "good", "great", "ability", "skills", "knowledge",
    "understanding", "background", "proficiency", "expertise", "familiarity",
    "equivalent", "related", "similar", "relevant", "preferred", "required",
    "plus", "bonus", "nice", "minimum", "least", "more", "than", "such",
    "including", "especially", "particularly", "specifically", "general",
    "various", "multiple", "large", "scale", "high", "low", "new", "modern",
    "cross", "functional", "fast", "paced", "startup", "environment",
    "business", "technical", "analytical", "quantitative", "statistical",
    "communication", "verbal", "written", "interpersonal", "collaborative",
    "detail", "oriented", "self", "starter", "motivated", "driven",
}

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def load_known_skills(cur) -> set:
    cur.execute("SELECT lower(skill_name) FROM skills")
    return {r[0] for r in cur.fetchall()}

def extract_bullet_lines(description: str) -> List[str]:
    """Extract bullet point lines from a job description."""
    lines = description.splitlines()
    bullets = []
    for line in lines:
        line = line.strip()
        if re.match(r"^[\-\*\•\·]\s+", line) or re.match(r"^\d+[\.\)]\s+", line):
            bullets.append(re.sub(r"^[\-\*\•\·\d\.\)]+\s+", "", line))
    return bullets

def contains_known_skill(line: str, known_skills: set) -> bool:
    """Check if a line mentions at least one known skill."""
    line_lower = line.lower()
    return any(skill in line_lower for skill in known_skills if len(skill) > 2)

def extract_candidates_from_line(line: str) -> List[str]:
    """Extract potential skill terms from a line using signal patterns."""
    candidates = []
    for pattern in SKILL_SIGNAL_PATTERNS:
        matches = re.finditer(pattern, line, re.IGNORECASE)
        for m in matches:
            term = m.group(1).strip().rstrip(".,;:")
            if term:
                candidates.append(term)
    return candidates

def clean_candidate(term: str) -> str:
    """Normalize a candidate term."""
    term = re.sub(r"\s+", " ", term).strip()
    term = term.rstrip(".,;:()")
    return term

def is_valid_candidate(term: str, known_skills: set) -> bool:
    """Filter out garbage candidates."""
    if not term or len(term) < 2 or len(term) > 40:
        return False
    if term.lower() in EXCLUDE_TERMS:
        return False
    if term.lower() in known_skills:
        return False
    # Must start with a letter
    if not re.match(r"^[A-Za-z]", term):
        return False
    # Skip pure soft skill phrases
    soft_skill_patterns = [
        r"^(strong|excellent|good|great|effective)\s+",
        r"\bskills?\b", r"\babilities\b", r"\bexperience\b",
        r"^(ability to|capacity to|willingness to)",
    ]
    for pat in soft_skill_patterns:
        if re.search(pat, term, re.IGNORECASE):
            return False
    return True

def discover_candidates(cur, known_skills: set, min_jobs: int = 3) -> List[Tuple[str, int, int]]:
    """
    Scan Tier 1 job descriptions for skill candidates.
    Returns [(term, job_count, company_count)] sorted by job_count desc.
    """
    cur.execute("""
        SELECT jp.job_id, jp.description_text, c.company_name
        FROM job_postings jp
        LEFT JOIN companies c ON c.company_id = jp.company_id
        WHERE jp.data_tier = 1
          AND jp.description_text IS NOT NULL
          AND length(jp.description_text) > 500
          AND COALESCE(jp.data_quality, 'ok') = 'ok'
    """)
    jobs = cur.fetchall()

    # term -> set of job_ids, set of companies
    term_jobs: Dict[str, set] = defaultdict(set)
    term_companies: Dict[str, set] = defaultdict(set)

    for job in jobs:
        job_id = job["job_id"]
        desc = job["description_text"] or ""
        company = job["company_name"] or "unknown"

        bullets = extract_bullet_lines(desc)

        for line in bullets:
            # Only process lines that mention a known skill (co-occurrence)
            if not contains_known_skill(line, known_skills):
                continue

            candidates = extract_candidates_from_line(line)
            for raw in candidates:
                term = clean_candidate(raw)
                if is_valid_candidate(term, known_skills):
                    # Normalize to title case for consistency
                    normalized = term.title() if term.isupper() or term.islower() else term
                    term_jobs[normalized].add(job_id)
                    term_companies[normalized].add(company)

    # Filter by minimum job count and sort
    results = [
        (term, len(jobs), len(term_companies[term]))
        for term, jobs in term_jobs.items()
        if len(jobs) >= min_jobs
    ]
    results.sort(key=lambda x: x[1], reverse=True)
    return results

def promote_skill(cur, skill_name: str) -> bool:
    """Add a skill to the skills table."""
    import hashlib
    # Check not already there
    cur.execute("SELECT skill_id FROM skills WHERE lower(skill_name) = lower(%s)", (skill_name,))
    if cur.fetchone():
        print(f"  Already exists: {skill_name}")
        return False
    sid = "S" + hashlib.md5(skill_name.encode()).hexdigest()[:10]
    cur.execute("SELECT skill_id FROM skills WHERE skill_id = %s", (sid,))
    if cur.fetchone():
        sid = "S" + hashlib.md5((skill_name + "_v2").encode()).hexdigest()[:10]
    cur.execute("INSERT INTO skills (skill_id, skill_name) VALUES (%s, %s)", (sid, skill_name))
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40, help="Max candidates to show")
    ap.add_argument("--min-jobs", type=int, default=3, help="Minimum job appearances")
    ap.add_argument("--promote", action="store_true", help="Interactive promotion mode")
    args = ap.parse_args()

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=DictCursor)

    print("Loading known skills...")
    known_skills = load_known_skills(cur)
    print(f"  {len(known_skills)} known skills")

    print("Scanning job descriptions for candidates...")
    candidates = discover_candidates(cur, known_skills, min_jobs=args.min_jobs)
    print(f"  Found {len(candidates)} candidates appearing in {args.min_jobs}+ jobs\n")

    if not candidates:
        print("No candidates found.")
        return

    # Display results
    print(f"{'Rank':<5} {'Term':<35} {'Jobs':>6} {'Companies':>10}")
    print("-" * 60)
    for i, (term, job_count, company_count) in enumerate(candidates[:args.limit], 1):
        print(f"{i:<5} {term:<35} {job_count:>6} {company_count:>10}")

    if args.promote:
        print("\n--- PROMOTION MODE ---")
        print("For each candidate enter: y=promote, n=skip, q=quit\n")
        promoted = []
        for term, job_count, company_count in candidates[:args.limit]:
            response = input(f"Promote '{term}' ({job_count} jobs, {company_count} companies)? [y/n/q]: ").strip().lower()
            if response == 'q':
                break
            if response == 'y':
                if promote_skill(cur, term):
                    promoted.append(term)
                    print(f"  ✅ Promoted: {term}")

        if promoted:
            conn.commit()
            print(f"\nPromoted {len(promoted)} skills: {', '.join(promoted)}")
            print("Run enrich_job_postings.py --apply --rescan-skills to extract them from existing jobs")
        else:
            conn.rollback()
            print("Nothing promoted.")
    else:
        conn.rollback()
        print(f"\nRun with --promote to interactively add skills to your allowlist")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
