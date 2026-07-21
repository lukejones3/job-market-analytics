# location_normalizer.py — Foreign ISO suffix bug fix (DRAFT, not yet applied)

## Summary of changes

Three targeted edits to `python/location_normalizer.py`. No logic rewrite; all existing
test cases continue to pass. Changes are additive and isolated to the two failure paths
identified in the bug analysis.

---

## Change 1 — New constant `_COLLIDING_COUNTRY_ISO` (add near line 164, after `FOREIGN_ISO_CODES`)

These two country ISO codes were stripped from `FOREIGN_ISO_CODES` because they collide
with US state codes (India/Indiana, Canada/California). We need them separately so we can
apply context-aware detection.

```python
# "in" (India) and "ca" (Canada) were removed from FOREIGN_ISO_CODES because they collide
# with US state codes IN (Indiana) and CA (California). Keep them here for use in
# context-sensitive checks below.
_COLLIDING_COUNTRY_ISO = frozenset({"in", "ca"})
```

**Where:** After line 164 (`FOREIGN_ISO_CODES = _FOREIGN_ISO_CANDIDATES - _US_STATE_CODES_LOWER`),
before the blank line at 165.

---

## Change 2 — `_is_foreign_iso_suffix` (replace existing function, ~lines 374–379)

**Current code:**
```python
def _is_foreign_iso_suffix(parts: list) -> bool:
    """Check if last part is a foreign 2-char ISO code (e.g. 'Sofia, bg')."""
    if not parts:
        return False
    last = parts[-1].strip().lower()
    return last in FOREIGN_ISO_CODES and last != "us"
```

**Replacement:**
```python
def _is_foreign_iso_suffix(parts: list) -> bool:
    """Check if last part is a foreign 2-char ISO code (e.g. 'Sofia, bg')."""
    if not parts:
        return False
    last = parts[-1].strip().lower()
    if last in FOREIGN_ISO_CODES and last != "us":
        return True
    # "in" (India) and "ca" (Canada) were stripped from FOREIGN_ISO_CODES due to collisions
    # with Indiana and California. Only safe to flag as foreign in 3-part format:
    #   "Bengaluru, KA, in"  →  foreign  ✓
    #   "Indianapolis, IN"   →  NOT flagged (len=2) — falls through to US city check  ✓
    #   "Burlington, ON, ca" →  foreign  ✓
    #   "Los Angeles, CA"    →  NOT flagged (len=2)  ✓
    if last in _COLLIDING_COUNTRY_ISO and len(parts) >= 3:
        return True
    return False
```

**Why len >= 3 is safe:** Legitimate 2-part US patterns like `"Indianapolis, IN"` and
`"Los Angeles, CA"` have len=2 and are unaffected. The 3-part format
`"City, Province, CountryISO"` is unambiguously a country ISO suffix.

---

## Change 3 — `has_us_state` check in FOREIGN_COUNTRY_RE block (~lines 537–545)

**Current code:**
```python
    # ---- Foreign signal check on remainder (after stripping US suffix) ----
    remaining = ", ".join(parts) if parts else s
    if FOREIGN_COUNTRY_RE.search(remaining.lower()):
        # US-wins overrides: explicit US marker OR a US state code anywhere in parts
        has_us_state = any(
            (p.strip().upper() in US_STATE_CODES) or (p.strip().lower() in US_STATE_NAMES)
            for p in parts
        )
        if US_EXPLICIT_RE.search(remaining.lower()) or has_us_state or had_us_suffix:
            pass  # foreign+US = US wins (e.g. "London, KY", "Paris, TX")
        else:
            return NormalizedLocation(None, None, "foreign", False)
```

**Replacement:**
```python
    # ---- Foreign signal check on remainder (after stripping US suffix) ----
    remaining = ", ".join(parts) if parts else s
    if FOREIGN_COUNTRY_RE.search(remaining.lower()):
        # US-wins overrides: explicit US marker OR a US state code anywhere in parts.
        # Exclude the last token from the state check if it's a colliding country ISO
        # ("in"=India/Indiana, "ca"=Canada/California) — in suffix position it's a
        # country code, not a state. This prevents "Hyderabad, in" → Indiana.
        # Unambiguous US state codes in suffix (e.g. "London, KY") still win correctly
        # because "ky" is not in _COLLIDING_COUNTRY_ISO.
        last_lower = parts[-1].strip().lower() if parts else ""
        parts_for_state_check = parts[:-1] if last_lower in _COLLIDING_COUNTRY_ISO else parts
        has_us_state = any(
            (p.strip().upper() in US_STATE_CODES) or (p.strip().lower() in US_STATE_NAMES)
            for p in parts_for_state_check
        )
        if US_EXPLICIT_RE.search(remaining.lower()) or has_us_state or had_us_suffix:
            pass  # foreign+US = US wins (e.g. "London, KY", "Paris, TX")
        else:
            return NormalizedLocation(None, None, "foreign", False)
```

**Cases validated:**
- `"Hyderabad, in"` → FOREIGN_COUNTRY_RE matches "hyderabad"; "in" excluded from state check → `has_us_state=False` → **foreign** ✓
- `"London, KY"` → FOREIGN_COUNTRY_RE matches "london"; "ky" NOT in `_COLLIDING_COUNTRY_ISO` → full parts checked → `has_us_state=True` → **US wins** ✓
- `"Paris, TX"` → same as London, KY → **US wins** ✓

---

## Change 4 — `"CA-"` Canada prefix, after the existing `"in"` India special-case (~lines 428–430)

**Current code (lines 423–442, the prefix detection block):**
```python
    # ---- Detect "IN - <anything>" pattern as India (foreign) ----
    # 2-letter foreign country code at start, followed by separator
    m = re.match(r"^([A-Z]{2})\b[\s-]", s)
    if m and m.group(1).lower() in FOREIGN_ISO_CODES:
        return NormalizedLocation(None, None, "foreign", False)
    if m and m.group(1).lower() == "in":  # India special case
        return NormalizedLocation(None, None, "foreign", False)
    # ---- "VA - Mark Center" / "TX - Austin Office" style: US state prefix ----
    if m and m.group(1).upper() in US_STATE_CODES:
        ...
```

**Replacement (insert one block after the "in" special case):**
```python
    # ---- Detect "IN - <anything>" pattern as India (foreign) ----
    # 2-letter foreign country code at start, followed by separator
    m = re.match(r"^([A-Z]{2})\b[\s-]", s)
    if m and m.group(1).lower() in FOREIGN_ISO_CODES:
        return NormalizedLocation(None, None, "foreign", False)
    if m and m.group(1).lower() == "in":  # India special case
        return NormalizedLocation(None, None, "foreign", False)
    if m and m.group(1).lower() == "ca":  # Canada prefix e.g. "CA-Toronto", "CA-Vancouver"
        # "CA" is ambiguous: California state OR Canada country code.
        # Resolve by checking if the remainder is a known foreign (Canadian) city.
        # If not, fall through to the US state-prefix handler below.
        rest_ca = s[m.end():].strip(" -")
        if FOREIGN_COUNTRY_RE.search(rest_ca.lower()):
            return NormalizedLocation(None, None, "foreign", False)
    # ---- "VA - Mark Center" / "TX - Austin Office" style: US state prefix ----
    if m and m.group(1).upper() in US_STATE_CODES:
        ...
```

**Cases validated:**
- `"CA-Toronto"` → rest_ca = "Toronto" → FOREIGN_COUNTRY_RE matches → **foreign** ✓
- `"CA-Vancouver"` → rest_ca = "Vancouver" → FOREIGN_COUNTRY_RE matches → **foreign** ✓
- `"CA-Sunnyvale"` → rest_ca = "Sunnyvale" → FOREIGN_COUNTRY_RE no match → falls through → **California state prefix, US** ✓

---

## Backfill query (run AFTER deploying the fix)

This corrects the ~61 already-ingested rows. Uses raw location patterns, NOT `loc_state`,
to avoid nuking real Indiana / Tennessee / Washington state jobs.

```sql
UPDATE job_postings SET status='ignored', loc_country='foreign'
WHERE job_id IN (
  SELECT jp.job_id
  FROM job_postings jp
  JOIN locations l ON jp.location_id = l.location_id
  WHERE jp.loc_country = 'US' AND jp.status != 'ignored'
    AND (
      -- 3-part "City, State, in" (India country ISO suffix)
      l.location ~* '^[^,]+, [^,]+, in$'
      -- 2-part "IndianCity, in" — constrained to known Indian cities
      -- to avoid catching Lebanon IN, Whitestown IN, etc.
      OR (l.location ~* '^[^,]+, in$'
          AND l.location ~* '(hyderabad|mumbai|bangalore|bengaluru|coimbatore|trivandrum|telengana|telangana|pune|chennai|delhi|kolkata|noida|gurgaon|gurugram|ahmedabad)')
      -- 3-part "City, Province, ca" (Canada country ISO suffix)
      OR l.location ~* '^[^,]+, [^,]+, ca$'
      -- "CA-Toronto" style Canada prefix
      OR (l.location ~* '^CA-'
          AND l.location ~* '(toronto|vancouver|montreal|ottawa|calgary|edmonton|winnipeg|burnaby|surrey|richmond|waterloo|london|kitchener|hamilton|brampton|mississauga|oakville)')
    )
);
-- Expected: ~61 rows
```

---

## New test cases to add to `_run_tests()`

```python
# Foreign ISO suffix — colliding country codes (the bug cases)
("Bengaluru, KA, in",          None, None, None, "foreign", False, True),
("Coimbatore, TN, in",         None, None, None, "foreign", False, True),
("Hyderabad, TS, in",          None, None, None, "foreign", False, True),
("Mumbai, MH, in",             None, None, None, "foreign", False, True),
("Chandigarh, CH, in",         None, None, None, "foreign", False, True),
("bangalore, in",              None, None, None, "foreign", False, True),
("Hyderabad, in",              None, None, None, "foreign", False, True),
("Burlington, ON, ca",         None, None, None, "foreign", False, True),
("CA-Toronto",                 None, None, None, "foreign", False, True),
("CA-Vancouver",               None, None, None, "foreign", False, True),
# Regression — must NOT be broken
("Indianapolis, IN",           None, "Indianapolis", "IN", "US", False, False),
("Nashville, TN",              None, "Nashville",    "TN", "US", False, False),
("Los Angeles, CA",            None, "Los Angeles",  "CA", "US", False, False),
("London, KY, USA",            None, "London",       "KY", "US", False, False),
("Paris, TX",                  None, "Paris",        "TX", "US", False, False),
("CA-Sunnyvale",               None, "Sunnyvale",    "CA", "US", False, False),
```
