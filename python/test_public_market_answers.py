from python.public_market_answers import PUBLIC_MARKET_ANSWER_SLUGS


def test_answer_inventory_is_curated_and_covers_demand_salary_and_behavior():
    assert 6 <= len(PUBLIC_MARKET_ANSWER_SLUGS) <= 12
    assert "data-analyst-job-market" in PUBLIC_MARKET_ANSWER_SLUGS
    assert "job-market-salary-transparency" in PUBLIC_MARKET_ANSWER_SLUGS
    assert "fastest-growing-company-hiring" in PUBLIC_MARKET_ANSWER_SLUGS
    assert "companies-with-most-verified-reposts" in PUBLIC_MARKET_ANSWER_SLUGS
