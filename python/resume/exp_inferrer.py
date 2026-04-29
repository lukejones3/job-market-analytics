"""Infer experience level from resume text.

Four signals, weighted by reliability:
  1. Years-of-experience phrases ("8+ years", "5 years of experience") — strongest
  2. Date-range parsing of work history ("Aug 2025 – Present") — strong
  3. Job titles in experience section ("Senior Engineer", "Manager") — medium
  4. Default to "mid" if no clear signal — conservative

Returns one of: entry, associate, mid, senior
"""

import re
from datetime import datetime
from typing import Optional, List, Tuple


# YOE patterns — match "5 years", "5+ years", "5-7 years", "five years"
YOE_PATTERNS = [
    re.compile(r"\b(\d{1,2})\s*\+?\s*(?:to\s*\d+)?\s*(?:-\s*\d+)?\s*years?\s+of\s+(?:experience|exp|professional)", re.I),
    re.compile(r"\b(\d{1,2})\s*\+\s*years?\b", re.I),
    re.compile(r"\b(?:over|more than)\s+(\d{1,2})\s+years?\b", re.I),
]

# Senior title indicators (in experience/role sections)
SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|head\s+of|director|"
    r"manager|vp|vice\s+president|chief|distinguished|fellow)\b",
    re.I,
)

# Entry indicators
ENTRY_TITLE_RE = re.compile(
    r"\b(intern|internship|junior|jr\.?|new\s+grad|graduate\s+(?:program|student)|"
    r"apprentice|trainee|early\s+career)\b",
    re.I,
)

# Associate indicators
ASSOC_TITLE_RE = re.compile(r"\bassociate\b", re.I)

# Date-range patterns: "Aug 2025 – Present", "August 2025 - Current",
# "08/2025 - Present", "2023 - 2025", "Jan 2020 – Mar 2024"
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

DATE_RANGE_RE = re.compile(
    r"""
    (?:
        # "Aug 2025" or "August 2025" or "08/2025" or "8/2025"
        (?P<m1>jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december|\d{1,2})
        [\s/\.,-]*
        (?P<y1>20\d{2}|19\d{2})
    |
        # bare year: "2023"
        (?P<y1b>20\d{2}|19\d{2})
    )
    \s*
    (?:[-–—to]+|\bto\b)
    \s*
    (?:
        # "Present" / "Current" / "Now"
        (?P<present>present|current|now)
    |
        # End date with month
        (?P<m2>jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december|\d{1,2})
        [\s/\.,-]*
        (?P<y2>20\d{2}|19\d{2})
    |
        # End date bare year
        (?P<y2b>20\d{2}|19\d{2})
    )
    """,
    re.I | re.VERBOSE,
)


def _max_yoe(text: str) -> Optional[int]:
    """Find the largest plausible YOE mentioned. Cap at 30 to filter junk."""
    max_yoe = None
    for pat in YOE_PATTERNS:
        for m in pat.finditer(text):
            try:
                yoe = int(m.group(1))
                if 0 <= yoe <= 30:
                    if max_yoe is None or yoe > max_yoe:
                        max_yoe = yoe
            except (ValueError, IndexError):
                continue
    return max_yoe


def _parse_month(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = s.strip().lower()
    if s.isdigit():
        try:
            n = int(s)
            return n if 1 <= n <= 12 else None
        except ValueError:
            return None
    return MONTHS.get(s)


def _date_to_months(year: int, month: Optional[int]) -> int:
    """Total months since year 0 (rough; only used for differences)."""
    return year * 12 + (month or 6)  # default to mid-year if no month given


def _extract_yoe_from_dates(text: str) -> Optional[float]:
    """Compute total months of work history from date ranges in text.

    Sums the durations of all detected ranges. Caps individual ranges at 30y
    to filter junk. Returns total years (float).

    Conservative: only counts ranges with year >= 1990 and where end >= start.
    """
    now = datetime.now()
    total_months = 0
    found_any = False

    for m in DATE_RANGE_RE.finditer(text):
        # Start
        y1 = m.group("y1") or m.group("y1b")
        if not y1:
            continue
        try:
            y1_int = int(y1)
        except ValueError:
            continue
        if y1_int < 1990 or y1_int > now.year:
            continue
        m1 = _parse_month(m.group("m1"))
        start_months = _date_to_months(y1_int, m1)

        # End
        if m.group("present"):
            end_y = now.year
            end_m = now.month
        else:
            y2 = m.group("y2") or m.group("y2b")
            if not y2:
                continue
            try:
                y2_int = int(y2)
            except ValueError:
                continue
            if y2_int < 1990 or y2_int > now.year + 1:
                continue
            m2 = _parse_month(m.group("m2"))
            end_y = y2_int
            end_m = m2

        end_months = _date_to_months(end_y, end_m)
        delta_months = end_months - start_months
        if delta_months <= 0 or delta_months > 360:  # filter nonsense (>30y range)
            continue
        total_months += delta_months
        found_any = True

    if not found_any:
        return None
    return total_months / 12.0


def _yoe_to_level(yoe: float) -> str:
    if yoe < 1:
        return "entry"
    if yoe < 3:
        return "associate"
    if yoe < 6:
        return "mid"
    return "senior"


def infer_experience_level(text: str) -> str:
    """Infer experience level from resume text.

    Args:
        text: Raw resume text

    Returns:
        One of: entry, associate, mid, senior
    """
    if not text or not text.strip():
        return "mid"

    # Signal 1: explicit YOE phrase (most reliable)
    yoe_explicit = _max_yoe(text)
    if yoe_explicit is not None:
        return _yoe_to_level(float(yoe_explicit))

    # Signal 2: date-range YOE inference (sum of durations)
    yoe_dates = _extract_yoe_from_dates(text)
    # Don't immediately return — combine with title signals first

    # Signal 3: title-based
    head = text[:3000]
    senior_count = len(SENIOR_TITLE_RE.findall(head))
    entry_count = len(ENTRY_TITLE_RE.findall(head))
    assoc_count = len(ASSOC_TITLE_RE.findall(head))

    # Combination logic:
    # Strong senior title signal overrides short tenure
    # (e.g. "Senior Engineer" with 2yr tenure = senior career-changer)
    if senior_count >= 2:
        return "senior"
    if senior_count >= 1 and entry_count == 0 and (yoe_dates is None or yoe_dates >= 4):
        return "senior"

    # Strong entry signal
    if entry_count >= 2:
        return "entry"
    if entry_count >= 1 and senior_count == 0:
        return "entry"

    # Date-based inference (when no strong title signal)
    if yoe_dates is not None:
        return _yoe_to_level(yoe_dates)

    # Associate signal as fallback
    if assoc_count >= 1 and senior_count == 0 and entry_count == 0:
        return "associate"

    # Default
    return "mid"
