"""Regression tests for the role admission boundary. No DB or network required."""
from role_scope import discovery_terms, evaluate_role


def test_core_expansion():
    expected = {
        "Treasury Analyst": ("finance", "treasury"),
        "Enterprise Risk Manager": ("finance", "risk"),
        "Product Operations Lead": ("product", "product_ops"),
        "Motion Designer": ("design", "motion"),
        "Supply Chain Analyst": ("ops", "supply_chain"),
    }
    for title, pair in expected.items():
        decision = evaluate_role(title, "")
        assert decision.admitted, (title, decision)
        assert (decision.domain, decision.category) == pair


def test_ambiguous_requires_evidence():
    pending = evaluate_role("Operations Manager", "")
    assert pending.status == "quarantine"
    good = evaluate_role("Operations Manager", "Own business operations strategy, KPI planning and cross-functional programs")
    assert good.status == "accepted_evidence"
    bad = evaluate_role("Operations Manager", "Manage a retail store, warehouse shifts and facilities")
    assert bad.status == "rejected"


def test_hard_exclusions_override_titles():
    assert evaluate_role("Roofing Sales Representative", "B2B sales").status == "rejected"
    assert evaluate_role("Clinical Data Entry Specialist", "SQL data entry").status == "rejected"


def test_unknown_knowledge_titles_are_observable_not_public():
    decision = evaluate_role("Compliance Analyst", "regulatory work")
    assert decision.status == "quarantine"
    assert decision.candidate
    assert not decision.admitted


def test_discovery_is_domain_balanced():
    terms = discovery_terms()
    assert len(terms) >= 35
    assert "treasury analyst" in terms
    assert "motion designer" in terms
    assert "supply chain analyst" in terms


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"PASS {name}")
