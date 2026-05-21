#!/usr/bin/env python3
"""
Shared text utilities: boilerplate detection and description cleaning.

All ML systems that consume job description text import from here so they
use identical cleaned text — matching what embed_jobs.py produces.

Do NOT modify embed_jobs.py (per project constraints); this module is the
canonical implementation for Phase 1 (classifier) and Phase 2 (skill extraction).
"""

import re
from collections import Counter
from typing import Dict, List, Set

CHUNK_SIZE = 150
MIN_JOBS_FOR_DEDUP = 3
BOILERPLATE_THRESHOLD = 0.40


def normalize_chunk(text: str) -> str:
    return re.sub(r'\s+', ' ', text.lower().strip())


def chunkify(text: str, size: int = CHUNK_SIZE) -> List[str]:
    if not text:
        return []
    chunks = []
    step = size // 2
    for i in range(0, len(text), step):
        chunk = text[i:i + size]
        if len(chunk) >= size // 2:
            chunks.append(chunk)
    return chunks


def find_boilerplate(descriptions: List[str]) -> Set[str]:
    if len(descriptions) < MIN_JOBS_FOR_DEDUP:
        return set()
    chunk_doc_count: Counter = Counter()
    for desc in descriptions:
        chunks_in_doc: Set[str] = set()
        for chunk in chunkify(desc or ''):
            normalized = normalize_chunk(chunk)
            if len(normalized) >= 50:
                chunks_in_doc.add(normalized)
        for chunk in chunks_in_doc:
            chunk_doc_count[chunk] += 1
    threshold = max(2, int(len(descriptions) * BOILERPLATE_THRESHOLD))
    return {chunk for chunk, count in chunk_doc_count.items() if count >= threshold}


def strip_boilerplate(text: str, boilerplate_set: Set[str]) -> str:
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


def compute_boilerplate_sets(company_descs: Dict[str, List[str]]) -> Dict[str, Set[str]]:
    """Build per-company boilerplate sets from {company_id: [descriptions]}."""
    return {cid: find_boilerplate(descs) for cid, descs in company_descs.items()}


def build_text(title: str, description: str, boilerplate_set: Set[str],
               max_chars: int = 2500) -> str:
    """Title + boilerplate-stripped description, same budget as embed_jobs.py."""
    title_clean = (title or '')[:200]
    desc_stripped = strip_boilerplate(description or '', boilerplate_set)
    desc_clean = desc_stripped[:max_chars - len(title_clean) - 2]
    return f"{title_clean}. {desc_clean}"


def build_text_for_classifier(title: str, description: str, boilerplate_set: Set[str],
                               desc_chars: int = 1500) -> str:
    """Title + generous description window for seniority/skill cue coverage.

    Uses a separate desc_chars budget (not shared with title) so the full
    1500-char description window is always available regardless of title length.
    Seniority cues ('5+ years', 'you will mentor', 'entry-level') are scattered
    through the JD, not just the opening paragraph.
    """
    title_clean = (title or '')[:200]
    desc_stripped = strip_boilerplate(description or '', boilerplate_set)
    desc_clean = desc_stripped[:desc_chars]
    return f"{title_clean}. {desc_clean}"
