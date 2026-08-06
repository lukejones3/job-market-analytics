from decimal import Decimal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from extract_salaries import annualize, salary_windows


def test_annualizes_supported_periods():
    assert annualize(Decimal("120000"), "year") == Decimal("120000")
    assert annualize(Decimal("6000"), "month") == Decimal("72000")
    assert annualize(Decimal("50"), "hour") == Decimal("104000")


def test_rejects_implausible_annual_values():
    assert annualize(Decimal("2"), "hour") is None
    assert annualize(Decimal("90000"), "month") is None
    assert annualize(Decimal("50"), "week") is None


def test_salary_windows_keeps_pay_context_and_drops_unrelated_money():
    text = "Raised $20,000,000 in funding. " + ("about us " * 200) + "Salary range: $90,000 - $120,000 annually."
    result = salary_windows(text)
    assert "Salary range" in result
    assert "$90,000" in result
    assert "funding" not in result
