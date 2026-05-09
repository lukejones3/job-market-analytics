"""
location_normalizer.py — single source of truth for location parsing.

Takes whatever messy string each source emits and returns a normalized
(city, state, country, is_remote) tuple.

Goals:
- Default to honest "unknown" rather than guessing
- Drop foreign jobs (country != 'US' and not unknown-remote) at ingestion
- Canonicalize NYC + SF variants only; everything else stays as-written but cleaned
- Workday office code stripping ("Mountain View (US-MTV-EMF680)" → "Mountain View")
- "N Locations" / "United States" / bare state-only → null city
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ---------- Canonical city mapping (V1: NY + SF only per Luke) ----------

NEW_YORK_VARIANTS = {
    "new york", "new york city", "nyc", "manhattan", "brooklyn",
    "queens", "bronx", "the bronx", "staten island",
    "ny ny", "ny, ny",
}

SAN_FRANCISCO_VARIANTS = {
    "san francisco", "sf", "south san francisco", "ssf",
    "san francisco bay area", "sf bay area", "the bay area",
    "soma",  # neighborhood
}

# ---------- US state codes/names ----------

US_STATE_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC","PR",
}

US_STATE_NAMES = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
    "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
    "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA",
    "kansas":"KS","kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD",
    "massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS",
    "missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV","new hampshire":"NH",
    "new jersey":"NJ","new mexico":"NM","new york":"NY","north carolina":"NC",
    "north dakota":"ND","ohio":"OH","oklahoma":"OK","oregon":"OR","pennsylvania":"PA",
    "rhode island":"RI","south carolina":"SC","south dakota":"SD","tennessee":"TN",
    "texas":"TX","utah":"UT","vermont":"VT","virginia":"VA","washington":"WA",
    "west virginia":"WV","wisconsin":"WI","wyoming":"WY",
    "district of columbia":"DC","washington dc":"DC","washington d.c.":"DC",
    "puerto rico":"PR",
}

# ---------- Foreign country signals ----------

# Country keywords (whole words). If found in a non-remote location string,
# we mark country='foreign' and the harvester drops the job.
FOREIGN_COUNTRY_RE = re.compile(
    r"\b("
    # Country names
    r"canada|united kingdom|uk|england|scotland|wales|ireland|northern ireland|"
    r"india|mexico|australia|new zealand|"
    r"singapore|germany|france|netherlands|holland|spain|portugal|"
    r"brazil|argentina|colombia|chile|peru|venezuela|costa rica|panama|guatemala|"
    r"japan|china|south korea|korea|taiwan|hong kong|thailand|vietnam|indonesia|malaysia|philippines|"
    r"poland|czech republic|czechia|slovakia|hungary|romania|bulgaria|croatia|serbia|slovenia|"
    r"sweden|denmark|norway|finland|iceland|"
    r"israel|uae|united arab emirates|saudi arabia|qatar|egypt|south africa|nigeria|kenya|"
    r"russia|ukraine|belarus|lithuania|latvia|estonia|"
    r"switzerland|austria|belgium|luxembourg|greece|turkey|cyprus|"
    # Region acronyms
    r"emea|apac|latam|latin america|"
    # Country codes (when standalone) - 2-letter codes that often appear as suffixes
    r"\.pt|\.de|\.fr|\.es|\.it|\.nl|\.uk|\.ie|\.in|\.br|\.mx|"
    # Major foreign cities (where the whole word is unambiguous)
    r"toronto|vancouver|montreal|ottawa|calgary|edmonton|"
    r"london|dublin|edinburgh|manchester|bristol|birmingham|cambridge|oxford|leeds|glasgow|"
    r"berlin|munich|hamburg|frankfurt|cologne|stuttgart|dusseldorf|düsseldorf|"
    r"paris|lyon|marseille|bordeaux|lille|rouen|toulouse|nice|levallois|levallois-perret|"
    r"amsterdam|rotterdam|eindhoven|the hague|utrecht|amersfoort|"
    r"madrid|barcelona|seville|valencia|"
    r"lisbon|lisboa|porto|"
    r"rome|milan|turin|naples|florence|"
    r"stockholm|gothenburg|göteborg|malmö|malmo|"
    r"copenhagen|aarhus|oslo|helsinki|"
    r"warsaw|warszawa|krakow|kraków|gdansk|wroclaw|"
    r"prague|brno|budapest|bucharest|sofia|bratislava|kosice|"
    r"vienna|zurich|geneva|bern|basel|"
    r"athens|istanbul|ankara|tel aviv|jerusalem|haifa|dubai|abu dhabi|cairo|riyadh|"
    r"vilnius|riga|tallinn|kiev|kyiv|"
    r"bangalore|bengaluru|hyderabad|chennai|mumbai|delhi|new delhi|kolkata|pune|noida|gurgaon|gurugram|ahmedabad|"
    r"sydney|melbourne|brisbane|perth|adelaide|"
    r"singapore city|kuala lumpur|jakarta|manila|bangkok|ho chi minh|hanoi|"
    r"tokyo|osaka|kyoto|yokohama|nagoya|"
    r"beijing|shanghai|guangzhou|shenzhen|hangzhou|"
    r"seoul|busan|incheon|"
    r"sao paulo|são paulo|san paulo|rio de janeiro|brasilia|brasília|belo horizonte|"
    r"mexico city|cdmx|guadalajara|monterrey|"
    r"buenos aires|cordoba|rosario|"
    r"bogota|bogotá|medellin|medellín|cali|"
    r"santiago|valparaiso|"
    r"lima|cusco|"
    r"cape town|johannesburg|durban|nairobi|lagos|"
    r"lagunilla|"  # Costa Rica
    r"heredia|"  # Costa Rica
    r"iasi|cluj-napoca|cluj|timisoara|"  # Romania
    r"makati|taguig|quezon city|"  # Philippines additions
    r"oakville|mississauga|markham|kitchener|waterloo|brampton|hamilton|"  # Canada Ontario
    r"changhua|kaohsiung|taipei|"  # Taiwan
    r"pakistan|karachi|lahore|islamabad|"  # Pakistan
    r"\bitaly\b|"  # Italy as bare country name (escape needed since "italy" is short)
    r"zürich|"  # umlaut variant of Zurich
    r"montpellier|"  # France
    r"canary wharf|"  # London tech district
    r"cairo|"  # Egypt (already in list but ensure)
    # Workday-style foreign country codes
    r"in-[a-z]{2}|"  # India regions like IN-MH (Maharashtra)
    r"de-[a-z]{2}|fr-[a-z]{2}|uk-[a-z]{2}|ie-[a-z]{2}|"
    # Explicit international markers
    r"international|remote - international|remote international"
    r")\b",
    re.IGNORECASE,
)

# ---------- US explicit signals ----------

US_EXPLICIT_RE = re.compile(
    r"\b(united states|united states of america|usa|u\.s\.|u\.s\.a\.|u s a|america|us)\b",
    re.IGNORECASE,
)

# Foreign 2-char ISO country codes that show up as "City, ST, xx" suffix
# Foreign ISO country codes that show up as "City, ST, xx" suffix.
# Defensively defined: we list candidate codes, then filter out anything that
# collides with a US state code. This prevents future state-code collisions
# (Minnesota=MN/Mongolia, Montana=MT/Malta, Idaho=ID/Indonesia, etc.).
_FOREIGN_ISO_CANDIDATES = {
    "uk", "gb", "ie", "fr", "de", "it", "es", "pt", "nl", "be", "lu", "at", "ch",
    "se", "no", "dk", "fi", "is", "pl", "cz", "sk", "hu", "ro", "bg", "hr", "si",
    "rs", "ba", "mk", "al", "gr", "tr", "cy", "mt",
    "il", "sa", "ae", "qa", "bh", "om", "kw", "jo", "lb", "eg", "ma", "tn", "dz",
    "in", "pk", "bd", "lk", "np", "th", "vn", "id", "my", "sg", "ph", "tw", "kr",
    "jp", "cn", "hk", "mn",
    "au", "nz",
    "br", "ar", "cl", "co", "pe", "mx", "ve", "ec", "uy", "bo",
    "za", "ng", "ke", "et", "gh",
    "ru", "ua", "by", "lt", "lv", "ee",
    "ca",
}
# US state codes — defined here too because the regex set is imported from below
_US_STATE_CODES_LOWER = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia",
    "ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj",
    "nm","ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn","tx","ut","vt",
    "va","wa","wv","wi","wy","dc","pr",
}
# Final foreign ISO set: candidates minus US state collisions
FOREIGN_ISO_CODES = _FOREIGN_ISO_CANDIDATES - _US_STATE_CODES_LOWER

# "in" (India) and "ca" (Canada) were removed from FOREIGN_ISO_CODES because they collide
# with US state codes IN (Indiana) and CA (California). Keep them here for context-sensitive checks.
_COLLIDING_COUNTRY_ISO = frozenset({"in", "ca"})

# Workday office code suffix: "Mountain View (US-MTV-EMF680)"
WORKDAY_OFFICE_SUFFIX_RE = re.compile(r"\s*\((?:US-)[A-Z]{2,4}-?[A-Z0-9-]+\)\s*$")

# Generic parenthetical office descriptor: "City (HQ)", "City (Office)", "City (One Bangkok Office)"
PAREN_DESCRIPTOR_RE = re.compile(r"\s*\([^)]+\)\s*$")

# Trailing "Office" / "- Office" / "- HQ" / "Headquarters" descriptors
OFFICE_SUFFIX_RE = re.compile(r"\s*-?\s*(?:office|hq|headquarters|main office|corp|corporate)\s*$", re.IGNORECASE)

# Generic dash-separator office descriptors: "Bucharest - Dacia One"
DASH_OFFICE_RE = re.compile(r"\s+-\s+[A-Za-z][A-Za-z0-9\s]+$")

# US cities allowlist — top US metros that appear as bare strings.
# Maps lowercase city → state code. NOT exhaustive — only high-confidence matches.
US_CITIES = {
    # Tier 1 metros
    "new york": "NY", "los angeles": "CA", "chicago": "IL", "houston": "TX",
    "phoenix": "AZ", "philadelphia": "PA", "san antonio": "TX", "san diego": "CA",
    "dallas": "TX", "austin": "TX", "san jose": "CA", "fort worth": "TX",
    "jacksonville": "FL", "columbus": "OH", "indianapolis": "IN", "charlotte": "NC",
    "san francisco": "CA", "seattle": "WA", "denver": "CO", "washington": "DC",
    "boston": "MA", "el paso": "TX", "nashville": "TN", "detroit": "MI",
    "oklahoma city": "OK", "portland": "OR", "las vegas": "NV", "memphis": "TN",
    "louisville": "KY", "baltimore": "MD", "milwaukee": "WI", "albuquerque": "NM",
    "tucson": "AZ", "fresno": "CA", "sacramento": "CA", "kansas city": "MO",
    "atlanta": "GA", "miami": "FL", "raleigh": "NC", "omaha": "NE", "minneapolis": "MN",
    "tulsa": "OK", "cleveland": "OH", "wichita": "KS", "arlington": "VA",
    "tampa": "FL", "new orleans": "LA", "honolulu": "HI", "anaheim": "CA",
    "santa ana": "CA", "saint louis": "MO", "st. louis": "MO", "st louis": "MO",
    "pittsburgh": "PA", "cincinnati": "OH", "anchorage": "AK", "henderson": "NV",
    "stockton": "CA", "riverside": "CA", "lexington": "KY", "corpus christi": "TX",
    # Tech/data hubs commonly seen
    "mountain view": "CA", "palo alto": "CA", "menlo park": "CA",
    "redwood city": "CA", "sunnyvale": "CA", "santa clara": "CA",
    "cupertino": "CA", "san mateo": "CA", "redmond": "WA", "bellevue": "WA",
    "cambridge": "MA", "somerville": "MA", "chevy chase": "MD", "bethesda": "MD",
    "reston": "VA", "tysons": "VA", "tysons corner": "VA", "mclean": "VA",
    "alexandria": "VA", "arlington": "VA", "herndon": "VA",
    "raleigh-durham": "NC", "durham": "NC", "research triangle park": "NC",
    "boulder": "CO", "ann arbor": "MI",
    # Commonly seen secondary cities
    "richmond": "VA", "columbus": "OH", "minneapolis": "MN", "saint paul": "MN",
    "st. paul": "MN", "irvine": "CA", "long beach": "CA", "oakland": "CA",
    "newark": "NJ", "jersey city": "NJ", "hoboken": "NJ", "buffalo": "NY",
    "rochester": "NY", "albany": "NY", "syracuse": "NY", "yonkers": "NY",
    "white plains": "NY", "stamford": "CT", "hartford": "CT", "new haven": "CT",
    "providence": "RI", "manchester": "NH", "portland": "ME",
    "des moines": "IA", "madison": "WI", "salt lake city": "UT",
    "boise": "ID", "spokane": "WA", "tacoma": "WA",
    "orlando": "FL", "fort lauderdale": "FL", "saint petersburg": "FL",
    "st. petersburg": "FL", "tallahassee": "FL", "gainesville": "FL",
    "savannah": "GA", "augusta": "GA", "macon": "GA", "athens": "GA",  # athens conflicts w/ Greek
    "birmingham": "AL", "huntsville": "AL", "mobile": "AL", "montgomery": "AL",
    "knoxville": "TN", "chattanooga": "TN", "clarksville": "TN",
    "lexington": "KY", "frankfort": "KY",
    "richmond": "VA", "norfolk": "VA", "virginia beach": "VA", "chesapeake": "VA",
    "charleston": "SC", "columbia": "SC", "greenville": "SC",
    "fayetteville": "NC", "asheville": "NC", "winston-salem": "NC", "greensboro": "NC",
    "baton rouge": "LA", "shreveport": "LA", "lafayette": "LA",
    "little rock": "AR", "fort smith": "AR",
    "tulsa": "OK", "norman": "OK",
    "wichita": "KS", "topeka": "KS", "overland park": "KS",
    "lincoln": "NE",
    "fargo": "ND", "bismarck": "ND",
    "sioux falls": "SD",
    "billings": "MT", "missoula": "MT",
    "cheyenne": "WY",
    "salt lake city": "UT", "provo": "UT",
    "reno": "NV", "carson city": "NV",
    "vegas": "NV",
    "scottsdale": "AZ", "tempe": "AZ", "mesa": "AZ", "chandler": "AZ", "gilbert": "AZ",
    "albuquerque": "NM", "santa fe": "NM",
    "colorado springs": "CO", "fort collins": "CO", "aurora": "CO",
    "burlington": "VT",
    # Known data/finance/tech specific
    "wilmington": "DE", "dover": "DE",
    # Additions from production unknown-bucket audit
    "beverly hills": "CA", "cary": "NC", "lehi": "UT", "fremont": "CA",
    "east hanover": "NJ", "irving": "TX", "plano": "TX",
    "alpharetta": "GA", "marietta": "GA", "duluth": "GA",
    "bentonville": "AR", "rogers": "AR",
    "morrisville": "NC", "apex": "NC", "chapel hill": "NC",
    "westford": "MA", "waltham": "MA", "burlington": "MA",
    "ashburn": "VA", "fairfax": "VA",
    "providence": "RI",
    # Defense corridor
    "huntsville": "AL", "fort worth": "TX", "lexington": "MA",
    # Costa Mesa, etc
    "costa mesa": "CA", "irving": "TX", "plano": "TX", "frisco": "TX",
    "round rock": "TX", "the woodlands": "TX",
}

# "Workday-Internal-Job-Code: location" prefix junk
JUNK_PREFIX_RE = re.compile(r"^[A-Z]{2,4}-\d+:\s*", re.IGNORECASE)

# "N Locations" placeholders
N_LOCATIONS_RE = re.compile(r"^\d+\s+locations?$", re.IGNORECASE)


@dataclass
class NormalizedLocation:
    city: Optional[str]      # canonical or cleaned, or None for remote/unknown
    state: Optional[str]     # 2-char US state code, or None
    country: str             # 'US', 'foreign', or 'unknown'
    is_remote: bool

    def to_dict(self) -> dict:
        return {
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "is_remote": self.is_remote,
        }

    @property
    def should_drop(self) -> bool:
        """True if harvesters should skip this job. Drops anything explicitly foreign,
        whether remote or not — we don't want non-US jobs in the platform."""
        return self.country == "foreign"


def _canonical_city(raw: str) -> Optional[str]:
    """Map known variants to canonical form. Returns None if no canonical match."""
    norm = raw.strip().lower()
    if norm in NEW_YORK_VARIANTS:
        return "New York"
    if norm in SAN_FRANCISCO_VARIANTS:
        return "San Francisco"
    return None


def _clean_city(raw: str) -> str:
    """Light cleanup: strip Workday codes, junk prefixes, office descriptors, whitespace."""
    s = raw.strip()
    # Strip URL/markdown junk like '[USA.CA](http://USA.CA).Pleasanton'
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)\.?", r"\1.", s)
    # Strip Workday codes first (most specific)
    s = WORKDAY_OFFICE_SUFFIX_RE.sub("", s)
    # Strip generic parenthetical descriptors: "City (HQ)", "City (Office)", "Manila (One Ayala Tower 2)"
    s = PAREN_DESCRIPTOR_RE.sub("", s)
    # Strip square-bracket descriptors: "[Palo Alto, CA OR San Francisco, CA]"
    # Take only the first city if there's a multi-option block
    m_bracket = re.search(r"\[([^\]]+)\]", s)
    if m_bracket:
        inner = m_bracket.group(1)
        # If bracket contains ' OR ' / ' or ', take only the first option
        first = re.split(r"\s+(?:OR|or)\s+", inner)[0]
        s = s.replace(m_bracket.group(0), first.strip())
    # Normalize "Boston, Mass" / "Boston, Mass." → "Boston, MA" — DO THIS BEFORE USA strip
    s = re.sub(r",\s*Mass(?:\.|achusetts)?\b\.?", ", MA", s, flags=re.IGNORECASE)
    s = re.sub(r",\s*Calif(?:\.|ornia)?\b\.?", ", CA", s, flags=re.IGNORECASE)
    s = re.sub(r",\s*Wash(?:\.)?\b\.?", ", WA", s, flags=re.IGNORECASE)
    s = re.sub(r",\s*Tex(?:\.|as)?\b\.?", ", TX", s, flags=re.IGNORECASE)
    s = re.sub(r",\s*Penn(?:\.|sylvania)?\b\.?", ", PA", s, flags=re.IGNORECASE)
    s = re.sub(r",\s*Fla(?:\.|orida)?\b\.?", ", FL", s, flags=re.IGNORECASE)
    # Strip "Hybrid - " / "Onsite - " / "Remote - " prefixes (workplace_type leaked in)
    s = re.sub(r"^(?:hybrid|onsite|remote|in-office|in office)\s*[-:]\s*", "", s, flags=re.IGNORECASE)
    # Strip trailing " USA" / " United States" / " America" — only after a state code or comma
    # to avoid eating "United States of America"
    s = re.sub(r"([A-Z]{2}|,\s?\w)\s+(?:USA|United States(?: of America)?|America)\s*$", r"\1", s)
    # Strip trailing " - Hybrid" / " - Remote" / " - Onsite" / " - HQ"
    s = re.sub(r"\s*-\s*(?:hybrid|onsite|remote|hybrid \(.*?\)|in-?office)\s*$", "", s, flags=re.IGNORECASE)
    # Strip trailing "Office" / "HQ" / "Headquarters"
    s = OFFICE_SUFFIX_RE.sub("", s)
    # Strip "Campus" suffixes: "Phoenix Campus", "Phoenix Campus (Main)"
    s = re.sub(r"\s+Campus\s*$", "", s, flags=re.IGNORECASE)
    # Strip junk ID prefixes like "ABC-1234: "
    s = JUNK_PREFIX_RE.sub("", s)
    # Strip descriptor-prefix-then-state pattern: "Headquarters - NY" → "NY", "Office - Washington-Seattle" → "Washington-Seattle"
    m_desc = re.match(r"^(?:headquarters|office|hq|main office|corp|corporate|main campus)\s*[-:]\s*(.+)$", s, re.IGNORECASE)
    if m_desc:
        s = m_desc.group(1).strip()
    # Normalize dash-as-separator: but ONLY in safe cases
    # "Austin- TX" → "Austin, TX" (dash with no space after letter then 2-char state)
    s = re.sub(r"([A-Za-z])-\s*([A-Z]{2})\b(?!-)", r"\1, \2", s)
    # NOTE: "USA - X" / "US - X" handled later by the USA-prefix logic — leave intact
    # Strip address number prefixes: "MN - St Paul, 10 River Park Plaza" → "MN - St Paul"
    s = re.sub(r",\s*\d+\s+[A-Z][A-Za-z0-9\s]+(?:Plaza|Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Way|Lane|Ln|Center|Centre)\.?$", "", s, flags=re.IGNORECASE)
    # Strip address suffix at end: "New York, 745 7th Avenue" → "New York"
    s = re.sub(r",\s*\d+\s+[A-Z0-9].*$", "", s)
    # Strip dash-separator office descriptors: "Bucharest - Dacia One" → "Bucharest"
    # Carefully — don't strip when first part is short like "USA" or "IN" (country codes that should be kept whole)
    m = re.match(r"^([^,]+?)\s+-\s+([A-Z][A-Za-z0-9 ]+)$", s)
    if m:
        first_part = m.group(1).strip()
        second_part = m.group(2).strip()
        first_lower = first_part.lower()
        second_lower = second_part.lower()
        is_country_prefix = first_lower in {"us", "usa", "in", "uk", "fr", "de", "es", "it"} or first_part.upper() == first_part
        office_indicators = {"hq", "office", "campus", "main", "headquarters", "corp", "corporate"}
        first_looks_like_office = any(ind in first_lower for ind in office_indicators)
        second_looks_like_office = any(ind in second_lower for ind in office_indicators)
        # If FIRST part has office indicators and SECOND looks like a real city, prefer second
        # (e.g. "DISCO Headquarters - Austin" → "Austin")
        if first_looks_like_office and not second_looks_like_office and not is_country_prefix:
            if second_lower in US_CITIES or _canonical_city(second_part):
                s = second_part
            elif "," not in raw[:m.end(1)]:
                s = second_part  # second part is likely a city
        elif second_looks_like_office and not is_country_prefix and "," not in raw[:m.end(1)]:
            s = first_part
        elif not is_country_prefix and len(second_part.split()) <= 3 and len(first_part) >= 4 and "," not in raw[:m.end(1)]:
            # Generic dash-suffix descriptor (only when first part is longer than country code)
            s = first_part
    s = re.sub(r"\s+", " ", s)
    return s.strip(" ,;-")


def _is_foreign_iso_suffix(parts: list) -> bool:
    """Check if last part is a foreign 2-char ISO code (e.g. 'Sofia, bg')."""
    if not parts:
        return False
    last = parts[-1].strip().lower()
    if last in FOREIGN_ISO_CODES and last != "us":
        return True
    # "in" (India) and "ca" (Canada) were stripped from FOREIGN_ISO_CODES due to collisions
    # with Indiana and California. Only safe to flag as foreign in 3-part format:
    #   "Bengaluru, KA, in"  → foreign  ✓
    #   "Indianapolis, IN"   → NOT flagged (len=2), falls through to US city check  ✓
    if last in _COLLIDING_COUNTRY_ISO and len(parts) >= 3:
        return True
    return False


def normalize_location(
    raw: Optional[str],
    workplace_type: Optional[str] = None,
) -> NormalizedLocation:
    """Single source of truth. Takes ANY messy location string + optional
    workplace_type, returns canonical fields."""
    is_remote = (workplace_type or "").lower() == "remote"

    # ---- Empty / null ----
    if not raw or not raw.strip():
        if is_remote:
            return NormalizedLocation(None, None, "US", True)
        return NormalizedLocation(None, None, "unknown", False)

    raw_clean = raw.strip()

    # ---- Recognize 'Remote' prefix even without workplace_type signal ----
    if not is_remote and re.match(r"^remote\b", raw_clean, re.IGNORECASE):
        rest = raw_clean[6:].strip(" -()")
        if not rest or US_EXPLICIT_RE.search(rest.lower()):
            return NormalizedLocation(None, None, "US", True)
        if FOREIGN_COUNTRY_RE.search(rest.lower()):
            return NormalizedLocation(None, None, "foreign", True)
        # Generic "Remote - <something>" — assume US-remote
        return NormalizedLocation(None, None, "US", True)

    # "N Locations" placeholder
    if N_LOCATIONS_RE.match(raw_clean):
        return NormalizedLocation(None, None, "unknown", is_remote)

    # ---- Remote shortcut ----
    if is_remote:
        if FOREIGN_COUNTRY_RE.search(raw_clean.lower()) and not US_EXPLICIT_RE.search(raw_clean.lower()):
            return NormalizedLocation(None, None, "foreign", True)
        return NormalizedLocation(None, None, "US", True)

    # ---- Strip office descriptors ----
    s = _clean_city(raw_clean)
    if not s:
        return NormalizedLocation(None, None, "unknown", False)

    # ---- Detect "IN - <anything>" pattern as India (foreign) ----
    # 2-letter foreign country code at start, followed by separator
    m = re.match(r"^([A-Z]{2})\b[\s-]", s)
    if m and m.group(1).lower() in FOREIGN_ISO_CODES:
        return NormalizedLocation(None, None, "foreign", False)
    if m and m.group(1).lower() == "in":  # India special case
        return NormalizedLocation(None, None, "foreign", False)
    if m and m.group(1).lower() == "ca":  # Canada prefix e.g. "CA-Toronto", "CA-Vancouver"
        rest_ca = s[m.end():].strip(" -")
        if FOREIGN_COUNTRY_RE.search(rest_ca.lower()):
            return NormalizedLocation(None, None, "foreign", False)
    # ---- "VA - Mark Center" / "TX - Austin Office" style: US state prefix ----
    if m and m.group(1).upper() in US_STATE_CODES:
        state_p = m.group(1).upper()
        rest = s[m.end():].strip(" -")
        # Try to extract a city from the rest
        rest_lower = rest.lower()
        if rest_lower in US_CITIES:
            return NormalizedLocation(rest, US_CITIES[rest_lower], "US", False)
        canon = _canonical_city(rest)
        if canon:
            return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
        # No clear city; just keep the state info
        return NormalizedLocation(rest if rest else None, state_p, "US", False)

    # ---- "USA HI Camp Smith" / "USA VA Chantilly" (space-separated USA <ST> <city>) ----
    m = re.match(r"^USA?\s+([A-Z]{2})\s+(.+)$", s, re.IGNORECASE)
    if m:
        state_c = m.group(1).upper()
        city_c = m.group(2).strip(" ,-")
        if state_c in US_STATE_CODES:
            canon = _canonical_city(city_c)
            if canon:
                return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
            return NormalizedLocation(city_c, state_c, "US", False)

    # ---- "TX, Coppell" / "VA, Chantilly" (state code first, comma-separated) ----
    m = re.match(r"^([A-Z]{2}),\s*(.+)$", s)
    if m and m.group(1) in US_STATE_CODES:
        state_c = m.group(1)
        city_c = m.group(2).strip()
        # Don't fire if city_c looks like another state (e.g. "NY, CA" is weird, drop)
        if city_c.upper() not in US_STATE_CODES:
            canon = _canonical_city(city_c)
            if canon:
                return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
            return NormalizedLocation(city_c, state_c, "US", False)

    # ---- "United States - Illinois - Abbott Park" (with spaces around dashes) ----
    m = re.match(r"^(?:USA?|United States(?: of America)?)\s*-\s*([A-Za-z][A-Za-z ]+?)\s*-\s*(.+)$", s, re.IGNORECASE)
    if m:
        possible_state = m.group(1).strip()
        rest = m.group(2).strip()
        state_c: Optional[str] = None
        if possible_state.upper() in US_STATE_CODES:
            state_c = possible_state.upper()
        elif possible_state.lower() in US_STATE_NAMES:
            state_c = US_STATE_NAMES[possible_state.lower()]
        if state_c:
            canon = _canonical_city(rest)
            if canon:
                return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
            return NormalizedLocation(rest, state_c, "US", False)

    # ---- Workday "USA-CA-San Mateo" format ----
    m = re.match(r"^USA?\s*[-/]\s*([A-Z]{2})\s*[-/]\s*(.+)$", s, re.IGNORECASE)
    if m:
        state_c = m.group(1).upper()
        city_c = m.group(2).strip(" ,-")
        if state_c in US_STATE_CODES:
            canon = _canonical_city(city_c)
            if canon:
                return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
            return NormalizedLocation(city_c, state_c, "US", False)

    # ---- "US CA San Francisco - HQ" pattern (space-separated) ----
    m = re.match(r"^US\s+([A-Z]{2})\s+(.+)$", s, re.IGNORECASE)
    if m:
        state_c = m.group(1).upper()
        city_c = m.group(2).strip(" ,-")
        if state_c in US_STATE_CODES:
            canon = _canonical_city(city_c)
            if canon:
                return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
            return NormalizedLocation(city_c, state_c, "US", False)

    # ---- "USA - Oakland" pattern (USA prefix, dash, then city) ----
    m = re.match(r"^USA?\s*-\s*(.+)$", s, re.IGNORECASE)
    if m:
        city_c = m.group(1).strip()
        # Recursively resolve the city part, but force country=US since we have USA prefix
        inner = normalize_location(city_c, None)
        if inner.country in ("US", "unknown"):
            return NormalizedLocation(inner.city or city_c, inner.state, "US", False)

    s_lower = s.lower()
    parts = [p.strip() for p in s.split(",") if p.strip()]

    # ---- Foreign ISO suffix check (e.g. "Sofia, bg") ----
    if _is_foreign_iso_suffix(parts):
        return NormalizedLocation(None, None, "foreign", False)

    # ---- Drop trailing "us"/"USA"/"United States" tokens ----
    had_us_suffix = False
    while parts and (
        parts[-1].lower() in {"us", "usa", "u.s.", "u.s.a.", "u s a", "america"}
        or US_EXPLICIT_RE.fullmatch(parts[-1].lower())
    ):
        parts.pop()
        had_us_suffix = True

    # If only "USA" and we stripped it, country=US, no city
    if not parts and had_us_suffix:
        return NormalizedLocation(None, None, "US", False)

    # ---- Foreign signal check on remainder (after stripping US suffix) ----
    remaining = ", ".join(parts) if parts else s
    if FOREIGN_COUNTRY_RE.search(remaining.lower()):
        # US-wins overrides: explicit US marker OR a US state code anywhere in parts.
        # Exclude the last token from the state check if it's a colliding country ISO
        # ("in"=India/Indiana, "ca"=Canada/California) — in suffix position it's a
        # country code, not a state. Unambiguous codes like "ky" still win correctly.
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

    # ---- Reversed "United States, Washington, Redmond" ----
    if len(parts) >= 3 and (
        US_EXPLICIT_RE.fullmatch(parts[0].lower())
        or parts[0].lower() in {"us", "usa"}
    ):
        possible_state = parts[1].strip()
        state_code_rev: Optional[str] = None
        if possible_state.upper() in US_STATE_CODES:
            state_code_rev = possible_state.upper()
        elif possible_state.lower() in US_STATE_NAMES:
            state_code_rev = US_STATE_NAMES[possible_state.lower()]
        if state_code_rev:
            city_rev = ", ".join(parts[2:]).strip()
            canon = _canonical_city(city_rev)
            if canon:
                return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
            return NormalizedLocation(city_rev, state_code_rev, "US", False)

    # ---- "City, ST" / "City, State Name" parsing ----
    state_code: Optional[str] = None
    city_part: Optional[str] = None

    if len(parts) >= 2:
        last = parts[-1].strip()
        if last.upper() in US_STATE_CODES:
            state_code = last.upper()
            city_part = ", ".join(parts[:-1]).strip()
        elif last.lower() in US_STATE_NAMES:
            state_code = US_STATE_NAMES[last.lower()]
            city_part = ", ".join(parts[:-1]).strip()
        elif len(parts) >= 3:
            # Try second-to-last as state (handles "City, ST, County" patterns)
            second_to_last = parts[-2].strip()
            if second_to_last.upper() in US_STATE_CODES:
                state_code = second_to_last.upper()
                city_part = parts[0].strip()
            elif second_to_last.lower() in US_STATE_NAMES:
                state_code = US_STATE_NAMES[second_to_last.lower()]
                city_part = parts[0].strip()
    elif len(parts) == 1:
        single = parts[0].strip()
        single_lower = single.lower()
        # Bare "Remote" without workplace_type — treat as US remote
        if single_lower in {"remote", "remote (us)", "remote us", "remote - us"}:
            return NormalizedLocation(None, None, "US", True)
        # "City ST" space-separated (e.g. "Austin TX")
        m_city_st = re.match(r"^([A-Z][a-zA-Z\s\.]+?)\s+([A-Z]{2})$", single)
        if m_city_st and m_city_st.group(2) in US_STATE_CODES:
            city_p = m_city_st.group(1).strip()
            state_p = m_city_st.group(2)
            canon = _canonical_city(city_p)
            if canon:
                return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
            return NormalizedLocation(city_p, state_p, "US", False)
        # Canonical (NYC/SF) variants — check FIRST so "New York Office" → "New York" not state=NY
        canon = _canonical_city(single)
        if canon:
            return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
        # Known US city allowlist — also check BEFORE state-name (so "Washington" the city wins over WA state when ambiguous)
        if single_lower in US_CITIES:
            return NormalizedLocation(single, US_CITIES[single_lower], "US", False)
        # Bare state code "NY"
        if single.upper() in US_STATE_CODES:
            return NormalizedLocation(None, single.upper(), "US", False)
        # Bare full state name
        if single_lower in US_STATE_NAMES:
            return NormalizedLocation(None, US_STATE_NAMES[single_lower], "US", False)
        # Unknown bare token
        return NormalizedLocation(single, None, "unknown", False)

    if state_code and city_part:
        canon = _canonical_city(city_part)
        if canon:
            return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
        return NormalizedLocation(city_part, state_code, "US", False)

    # ---- Final fallback ----
    canon = _canonical_city(s)
    if canon:
        return NormalizedLocation(canon, "NY" if canon == "New York" else "CA", "US", False)
    if s_lower in US_CITIES:
        return NormalizedLocation(s, US_CITIES[s_lower], "US", False)

    return NormalizedLocation(s, None, "unknown", False)


# ---------- Self-test ----------
# Run with: python -m location_normalizer

def _run_tests():
    cases = [
        # Remote
        ("Remote", "remote", None, None, "US", True, False),
        ("Remote - US", "remote", None, None, "US", True, False),
        ("Remote - International", "remote", None, None, "foreign", True, True),
        ("", "remote", None, None, "US", True, False),
        (None, "remote", None, None, "US", True, False),
        ("Remote (United States)", None, None, None, "US", True, False),  # parser correctly infers remote
        # Foreign drops
        ("Krakow", None, None, None, "foreign", False, True),
        ("Lisboa, Lisboa, pt", None, None, None, "foreign", False, True),
        ("Bordeaux", None, None, None, "foreign", False, True),
        ("Sao Paulo", None, None, None, "foreign", False, True),
        ("Pune, Gera Commerzone SEZ", None, None, None, "foreign", False, True),
        ("Bangalore", None, None, None, "foreign", False, True),
        ("Sofia, bg", None, None, None, "foreign", False, True),
        ("Sofia, Sofia City Province, bg", None, None, None, "foreign", False, True),
        ("Sofia, 23, bg", None, None, None, "foreign", False, True),
        ("Levallois-Perret, IDF, fr", None, None, None, "foreign", False, True),
        ("Riyadh, Riyadh Province, sa", None, None, None, "foreign", False, True),
        ("Lagunilla, Costa Rica", None, None, None, "foreign", False, True),
        ("Cape Town", None, None, None, "foreign", False, True),
        ("Bangkok (One Bangkok Office)", None, None, None, "foreign", False, True),
        ("Manila (One Ayala Tower 2)", None, None, None, "foreign", False, True),
        ("Bucharest - Dacia One", None, None, None, "foreign", False, True),
        ("Vilnius", None, None, None, "foreign", False, True),
        ("Warszawa", None, None, None, "foreign", False, True),
        ("Kuala Lumpur", None, None, None, "foreign", False, True),
        ("Croatia", None, None, None, "foreign", False, True),
        ("Bristol", None, None, None, "foreign", False, True),
        ("IN - TDC 1 (IN110)", None, None, None, "foreign", False, True),
        # Clean US
        ("San Francisco, CA", None, "San Francisco", "CA", "US", False, False),
        ("San Francisco, California", None, "San Francisco", "CA", "US", False, False),
        ("Manhattan, NY", None, "New York", "NY", "US", False, False),
        ("Brooklyn", None, "New York", "NY", "US", False, False),
        ("NYC", None, "New York", "NY", "US", False, False),
        ("New York City", None, "New York", "NY", "US", False, False),
        ("SF", None, "San Francisco", "CA", "US", False, False),
        ("Mountain View (US-MTV-EMF680)", None, "Mountain View", "CA", "US", False, False),
        ("Mclean, VA", None, "Mclean", "VA", "US", False, False),
        ("United States", None, None, None, "US", False, False),
        ("USA", None, None, None, "US", False, False),
        ("United States of America", None, None, None, "US", False, False),
        ("United States, Washington, Redmond", None, "Redmond", "WA", "US", False, False),
        ("NY", None, None, "NY", "US", False, False),
        # Bare US cities (allowlist)
        ("Chicago", None, "Chicago", "IL", "US", False, False),
        ("Boston", None, "Boston", "MA", "US", False, False),
        ("Denver", None, "Denver", "CO", "US", False, False),
        ("Seattle", None, "Seattle", "WA", "US", False, False),
        ("San Jose", None, "San Jose", "CA", "US", False, False),
        ("Los Angeles", None, "Los Angeles", "CA", "US", False, False),
        # Lowercase 'us' suffix
        ("Sunnyvale, CA, us", None, "Sunnyvale", "CA", "US", False, False),
        ("Charlotte, NC, us", None, "Charlotte", "NC", "US", False, False),
        ("Denver, CO, us", None, "Denver", "CO", "US", False, False),
        ("Chicago, IL, us", None, "Chicago", "IL", "US", False, False),
        ("Columbus, OH, us", None, "Columbus", "OH", "US", False, False),
        ("Richmond, VA, us", None, "Richmond", "VA", "US", False, False),
        # Office descriptors stripped
        ("San Francisco Office", None, "San Francisco", "CA", "US", False, False),
        ("New York Office", None, "New York", "NY", "US", False, False),
        ("Pittsburgh Office", None, "Pittsburgh", "PA", "US", False, False),
        ("Menlo Park - Office", None, "Menlo Park", "CA", "US", False, False),
        ("Costa Mesa, CA (HQ)", None, "Costa Mesa", "CA", "US", False, False),
        ("Palo Alto, CA (HQ)", None, "Palo Alto", "CA", "US", False, False),
        ("Mountain View, California, US", None, "Mountain View", "CA", "US", False, False),
        ("California - HQ", None, None, "CA", "US", False, False),
        ("Washington-Office", None, "Washington", "DC", "US", False, False),  # "Washington" first matches city allowlist → DC
        # Workday USA-XX-City
        ("USA-CA-San Mateo", None, "San Mateo", "CA", "US", False, False),
        ("US CA San Francisco - HQ", None, "San Francisco", "CA", "US", False, False),
        ("USA - Oakland", None, "Oakland", "CA", "US", False, False),
        # New York variants in different orders
        ("New York, NEW YORK, us", None, "New York", "NY", "US", False, False),
        ("New York, New York, United States of America", None, "New York", "NY", "US", False, False),
        # Junk
        ("2 Locations", None, None, None, "unknown", False, False),
        ("3 locations", None, None, None, "unknown", False, False),
        ("Headquarters", None, None, None, "unknown", False, False),
        ("BANCO INTER", None, "BANCO INTER", None, "unknown", False, False),
        # State codes that collide with foreign ISO codes (REGRESSION TESTS)
        ("Bentonville, AR", "hybrid", "Bentonville", "AR", "US", False, False),
        ("Boston, MA", "hybrid", "Boston", "MA", "US", False, False),
        ("Indianapolis, IN", None, "Indianapolis", "IN", "US", False, False),
        ("Denver, CO", None, "Denver", "CO", "US", False, False),
        ("Birmingham, AL", None, "Birmingham", "AL", "US", False, False),
        ("Wilmington, DE", None, "Wilmington", "DE", "US", False, False),
        ("Chicago, IL", None, "Chicago", "IL", "US", False, False),
        ("Boise, ID", None, "Boise", "ID", "US", False, False),
        ("Nashville, TN", None, "Nashville", "TN", "US", False, False),
        ("Aurora, IL", None, "Aurora", "IL", "US", False, False),
        ("Minneapolis, MN", None, "Minneapolis", "MN", "US", False, False),
        ("Saint Paul, MN", None, "Saint Paul", "MN", "US", False, False),
        ("Bozeman, MT", None, "Bozeman", "MT", "US", False, False),
        ("United States, MN", None, "United States", "MN", "US", False, False),
        # India still caught via prefix/Workday code
        ("IN-MH-Mumbai", None, None, None, "foreign", False, True),
        ("IN - TDC 1", None, None, None, "foreign", False, True),
        ("IN TN CHENNAI Home Office Capita Land", None, None, None, "foreign", False, True),
        # Israel still caught via city name
        ("Tel Aviv", None, None, None, "foreign", False, True),
        # Indonesia still caught via city name
        ("Jakarta", None, None, None, "foreign", False, True),
        # Tunisia/Morocco caught via country name (need to add)
        # Inverse-bucket patterns (US jobs that were getting tagged unknown)
        ("Austin- TX", None, "Austin", "TX", "US", False, False),
        ("Headquarters - NY", None, None, "NY", "US", False, False),
        ("Reston, VA - Hybrid", "hybrid", "Reston", "VA", "US", False, False),
        ("Hybrid - Denver", "hybrid", "Denver", "CO", "US", False, False),
        ("Boston, Mass", None, "Boston", "MA", "US", False, False),
        ("Austin TX - HQ", "hybrid", "Austin", "TX", "US", False, False),
        ("Sunnyvale, California USA", "hybrid", "Sunnyvale", "CA", "US", False, False),
        ("United States-Arizona-Chandler", None, "Chandler", "AZ", "US", False, False),
        ("Phoenix Campus (Main)", None, "Phoenix", "AZ", "US", False, False),
        ("Dallas, TX - Hybrid (3x in office/week)", "hybrid", "Dallas", "TX", "US", False, False),
        ("New York, 745 7th Avenue", None, "New York", "NY", "US", False, False),
        ("DISCO Headquarters - Austin", None, "Austin", "TX", "US", False, False),
        # USA <ST> <city> space-separated (USA HI Camp Smith / USA VA Chantilly)
        ("USA HI Camp Smith", "onsite", "Camp Smith", "HI", "US", False, False),
        ("USA VA Chantilly", "onsite", "Chantilly", "VA", "US", False, False),
        # State-code-first pattern
        ("TX, Coppell", "onsite", "Coppell", "TX", "US", False, False),
        ("VA, Reston", None, "Reston", "VA", "US", False, False),
        # Boston, Mass. with period
        ("Boston, Mass.", None, "Boston", "MA", "US", False, False),
        # New foreign cities (from production audit)
        ("Iasi", None, None, None, "foreign", False, True),
        ("Cluj-Napoca", None, None, None, "foreign", False, True),
        ("Pakistan", None, None, None, "foreign", False, True),
        ("Italy", None, None, None, "foreign", False, True),
        ("Makati", None, None, None, "foreign", False, True),
        ("Oakville", None, None, None, "foreign", False, True),
        ("Mississauga, ON", None, None, None, "foreign", False, True),
        ("Heredia, Costa Rica", None, None, None, "foreign", False, True),
        ("Changhua", None, None, None, "foreign", False, True),
        ("Montpellier", None, None, None, "foreign", False, True),
        # New US cities
        ("Beverly Hills", None, "Beverly Hills", "CA", "US", False, False),
        ("Cary, NC", None, "Cary", "NC", "US", False, False),
        ("Lehi, UT", None, "Lehi", "UT", "US", False, False),
        ("Fremont", None, "Fremont", "CA", "US", False, False),
        ("London, KY, USA", None, "London", "KY", "US", False, False),
        ("Los Angeles, USA", None, "Los Angeles", "CA", "US", False, False),
        # Foreign ISO suffix — colliding country codes (regression tests for the IN/CA bug)
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
        # Regression — must NOT be broken by the above fix
        ("Indianapolis, IN",           None, "Indianapolis", "IN", "US", False, False),
        ("Nashville, TN",              None, "Nashville",    "TN", "US", False, False),
        ("Los Angeles, CA",            None, "Los Angeles",  "CA", "US", False, False),
        ("London, KY, USA",            None, "London",       "KY", "US", False, False),
        ("Paris, TX",                  None, "Paris",        "TX", "US", False, False),
        ("CA-Sunnyvale",               None, "Sunnyvale",    "CA", "US", False, False),
    ]

    passed = 0
    failed = 0
    for case in cases:
        raw, wp, exp_city, exp_state, exp_country, exp_remote, exp_drop = case
        result = normalize_location(raw, wp)
        ok = (
            result.city == exp_city
            and result.state == exp_state
            and result.country == exp_country
            and result.is_remote == exp_remote
            and result.should_drop == exp_drop
        )
        status = "✓" if ok else "✗"
        print(f"  {status} {repr(raw):<48} wp={wp!r:<10} → city={result.city!r}, state={result.state!r}, country={result.country!r}, remote={result.is_remote}, drop={result.should_drop}")
        if not ok:
            print(f"      EXPECTED: city={exp_city!r}, state={exp_state!r}, country={exp_country!r}, remote={exp_remote}, drop={exp_drop}")
            failed += 1
        else:
            passed += 1
    print(f"\n{passed}/{passed+failed} passed")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_tests() else 1)
