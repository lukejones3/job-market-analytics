"""Evidence-first four-level job seniority classification.

This module intentionally does not use general-purpose semantic job embeddings.
Those embeddings conflate the applicant's level with stakeholders and company
language (for example, "present to senior leadership").
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

VERSION = "exp_v3.0.0"
LEVELS = {"entry", "associate", "mid", "senior", "unknown"}

_ENTRY_TITLE = re.compile(
    r"\b(?:intern(?:ship)?|co[ -]?op|junior|entry[ -]?level|new[ -]?grad(?:uate)?|"
    r"graduate program|early[ -]?career|apprentice|trainee)\b|\bjr\.?(?=\W|$)", re.I
)
_SENIOR_TITLE = re.compile(
    r"\b(?:senior|staff|principal|distinguished|fellow|lead)\b|\bsr\.?(?=\W|$)", re.I
)
_EXEC_TITLE = re.compile(
    r"\b(?:director|vice president|vp|head of|chief|c[a-z]o)\b", re.I
)
_ASSOCIATE_TITLE = re.compile(r"\bassociate\b", re.I)
_MID_TITLE = re.compile(r"\bmid[ -]?level\b", re.I)
_LEVEL_NUMBER = re.compile(r"(?:\b(?:level|grade)\s*)?\b(i|ii|iii|iv|v|[1-5])\b", re.I)

_REQUIRED_HEADINGS = re.compile(
    r"\b(?:minimum|required|basic|essential) qualifications?\b|\bwhat you(?:'|’)ll need\b|"
    r"\brequirements?\b|(?<!preferred )\bqualifications?\b", re.I
)
_PREFERRED_HEADINGS = re.compile(
    r"\bpreferred qualifications?\b|\bnice to have\b|\bbonus(?: points)?\b|\bdesired\b", re.I
)
_NEXT_SECTION = re.compile(
    r"\b(?:what you(?:'|’)ll do|responsibilities|about (?:us|the company|the role)|"
    r"benefits|compensation|salary|travel requirements|working conditions|why join)\b", re.I
)

_YEARS_PATTERNS = (
    re.compile(
        r"(?P<lo>\d{1,2})\s*(?:\+|(?:-|–|—|to)\s*(?P<hi>\d{1,2}))?\s*years?\s+"
        r"(?:of\s+)?(?:(?:relevant|related|professional|directly related|hands[ -]?on|"
        r"progressive|industry)\s+)?experience\b", re.I
    ),
    re.compile(
        r"(?:minimum(?: of)?|at least|requires?|required)\s+(?P<lo>\d{1,2})\s*"
        r"(?:\+|(?:-|–|—|to)\s*(?P<hi>\d{1,2}))?\s*years?\b", re.I
    ),
    re.compile(
        r"\bexperience\b[^.;:\n]{0,45}?\b(?P<lo>\d{1,2})\s*"
        r"(?:\+|(?:-|–|—|to)\s*(?P<hi>\d{1,2}))?\s*years?\b", re.I
    ),
    re.compile(
        r"(?P<lo>\d{1,2})\s*(?:\+|(?:-|–|—|to)\s*(?P<hi>\d{1,2}))?\s*years?\s+"
        r"(?:working|building|developing|designing|managing|leading|using|in|with|as)\b", re.I
    ),
)

_SENIOR_SCOPE = (
    ("people_management", re.compile(r"\b(?:manage|supervise|hire|develop) (?:a |the )?(?:team|people|direct reports)\b", re.I)),
    ("mentorship", re.compile(r"\bmentor(?:ing)? (?:junior |other )?(?:engineers|analysts|scientists|team members)\b", re.I)),
    ("org_strategy", re.compile(r"\b(?:set|define|own) (?:the )?(?:technical|data|product|departmental|organizational) strategy\b", re.I)),
    ("architecture_ownership", re.compile(r"\b(?:own|define|drive) (?:the )?(?:architecture|technical direction|roadmap)\b", re.I)),
)


@dataclass(frozen=True)
class YearsEvidence:
    minimum: int
    maximum: Optional[int]
    source: str
    text: str


@dataclass
class ExperienceDecision:
    level: str
    confidence: float
    management_level: str
    title_signal: str
    required_years: Optional[int] = None
    preferred_years: Optional[int] = None
    scope_signals: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    classifier_version: str = VERSION

    def evidence(self) -> dict:
        return asdict(self)


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _section_at(text: str, position: int) -> str:
    """Return the most recent qualification section before a candidate."""
    prefix = text[max(0, position - 2500):position]
    required = list(_REQUIRED_HEADINGS.finditer(prefix))
    preferred = list(_PREFERRED_HEADINGS.finditer(prefix))
    resets = list(_NEXT_SECTION.finditer(prefix))
    last_required = required[-1].start() if required else -1
    last_preferred = preferred[-1].start() if preferred else -1
    last_reset = resets[-1].start() if resets else -1
    if last_reset > max(last_required, last_preferred):
        return "unscoped"
    if last_preferred > last_required:
        return "preferred"
    if last_required >= 0:
        return "required"
    return "unscoped"


def extract_years(text: str) -> tuple[Optional[YearsEvidence], Optional[YearsEvidence]]:
    cleaned = _clean(text)
    found: list[YearsEvidence] = []
    occupied: list[tuple[int, int]] = []
    for pattern in _YEARS_PATTERNS:
        for match in pattern.finditer(cleaned):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            lo = int(match.group("lo"))
            hi_raw = match.groupdict().get("hi")
            hi = int(hi_raw) if hi_raw else None
            if lo > 15 or (hi is not None and hi > 20):
                continue
            context = cleaned[max(0, match.start() - 100):match.end() + 120]
            # Company-age statements are not applicant requirements.
            if re.search(r"\b(?:for|over|more than)\s+\d+\s+years?\b", context, re.I) and not re.search(
                r"\b(?:experience|required|qualification|you have|candidate)\b", context, re.I
            ):
                continue
            section = _section_at(cleaned, match.start())
            if section == "unscoped" and not re.search(
                r"\b(?:you|candidate|applicant|must|required|qualification|experience|we(?:'|’)re looking)\b",
                context, re.I,
            ):
                continue
            found.append(YearsEvidence(lo, hi, section, match.group(0)))
            occupied.append((match.start(), match.end()))

    required = [item for item in found if item.source == "required"]
    preferred = [item for item in found if item.source == "preferred"]
    unscoped = [item for item in found if item.source == "unscoped"]
    # The lowest explicit minimum controls accessibility. Prefer a scoped
    # requirement, then an unscoped candidate; never promote from preferred.
    req = min(required, key=lambda item: item.minimum) if required else (
        min(unscoped, key=lambda item: item.minimum) if unscoped else None
    )
    pref = min(preferred, key=lambda item: item.minimum) if preferred else None
    return req, pref


def _title_signal(title: str) -> tuple[str, Optional[str]]:
    title = _clean(title)
    # Composite senior titles must beat their subordinate token: Senior
    # Associate and Associate Director are senior, not associate.
    if _EXEC_TITLE.search(title) or _SENIOR_TITLE.search(title):
        return "explicit_senior", "senior"
    if _ENTRY_TITLE.search(title):
        return "explicit_entry", "entry"
    if _MID_TITLE.search(title):
        return "explicit_mid", "mid"
    if _ASSOCIATE_TITLE.search(title):
        return "explicit_associate", "associate"

    number = _LEVEL_NUMBER.search(title)
    if number:
        value = number.group(1).lower()
        mapped = {"i": "associate", "1": "associate", "ii": "mid", "2": "mid",
                  "iii": "senior", "3": "senior", "iv": "senior", "4": "senior",
                  "v": "senior", "5": "senior"}
        return f"title_level_{value}", mapped[value]
    return "neutral", None


def _management_level(title: str, description: str) -> str:
    title_clean = _clean(title)
    if _EXEC_TITLE.search(title_clean):
        return "executive"
    if re.search(r"\bmanager\b", title_clean) or re.search(
        r"\b(?:manage|supervise|hire) (?:a |the )?(?:team|people|direct reports)\b", description or "", re.I
    ):
        return "people_manager"
    if title_clean:
        return "individual_contributor"
    return "unknown"


def _years_level(years: int) -> str:
    if years <= 1:
        return "entry"
    if years == 2:
        return "associate"
    if years <= 4:
        return "mid"
    return "senior"


def classify_experience(title: str, description: str) -> ExperienceDecision:
    title_signal, title_level = _title_signal(title)
    required, preferred = extract_years(description)
    scope = [name for name, pattern in _SENIOR_SCOPE if pattern.search(description or "")]
    management = _management_level(title, description)
    conflicts: list[str] = []
    required_level = _years_level(required.minimum) if required else None

    if title_level:
        level = title_level
        confidence = 0.99
        if required_level and required_level != title_level:
            conflicts.append(f"title_{title_level}_vs_required_{required_level}")
            # Explicit advertised level remains authoritative; years evidence is
            # retained so consumers can see unusually accessible senior roles.
            confidence = 0.90
    elif required_level:
        level = required_level
        confidence = 0.96 if required.source == "required" else 0.88
    elif management == "people_manager" and len(scope) >= 1:
        level = "senior"
        confidence = 0.78
    elif len(scope) >= 2:
        level = "senior"
        confidence = 0.72
    else:
        level = "unknown"
        confidence = 0.35

    if preferred and required and preferred.minimum < required.minimum:
        conflicts.append("preferred_years_below_required_years")

    assert level in LEVELS
    return ExperienceDecision(
        level=level,
        confidence=confidence,
        management_level=management,
        title_signal=title_signal,
        required_years=required.minimum if required else None,
        preferred_years=preferred.minimum if preferred else None,
        scope_signals=scope,
        conflicts=conflicts,
    )
