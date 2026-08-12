"""Company-name admission and normalization at ATS ingestion boundaries."""
from __future__ import annotations

import json
import re
from pathlib import Path

_OVERRIDE_PATH = Path(__file__).resolve().parents[1] / "config" / "workday_company_overrides.json"
_ROLE_LIKE_COMPANY = re.compile(
    r"^(?:senior|sr\.?|lead|principal|staff|junior|jr\.?|associate)?\s*"
    r"(?:(?:sustainability|marketing|product|financial|finance|operations|risk|healthcare|clinical|"
    r"software|platform|quality|compliance|quantitative|research|business intelligence|bi|"
    r"machine learning|ml|ai|data|analytics)\s+){1,3}"
    r"(?:engineer|analyst|scientist|architect|developer|manager|consultant|specialist)"
    r"(?:\s+[ivx0-9]+)?s?$",
    re.I,
)
_LOCATOR_LIKE_COMPANY = re.compile(
    r"(?:^https?://|\|wd\d+\|(?:en|fr|de|ja)(?:-[a-z]{2})?$|/wd\d+/)", re.I
)


def workday_company_overrides() -> dict[str, str]:
    with _OVERRIDE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def is_plausible_company_name(name: str | None) -> bool:
    """Reject discovery artifacts that are job titles masquerading as employers."""
    clean = re.sub(r"\s+", " ", (name or "")).strip()
    if not clean or len(clean) < 2:
        return False
    if _LOCATOR_LIKE_COMPANY.search(clean):
        return False
    return _ROLE_LIKE_COMPANY.fullmatch(clean) is None


def resolved_workday_company_name(tenant: str, discovered_name: str | None) -> str:
    overrides = workday_company_overrides()
    if tenant.lower() in overrides:
        return overrides[tenant.lower()]
    if is_plausible_company_name(discovered_name):
        return re.sub(r"\s+", " ", discovered_name or "").strip()
    return tenant.replace("-", " ").replace("_", " ").title()
