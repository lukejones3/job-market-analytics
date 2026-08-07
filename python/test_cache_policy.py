"""Regression tests for the API cache allow-list and freshness metadata."""

import unittest
from pathlib import Path

from cache_policy import PUBLIC_INSIGHT_CACHE_CONTROL, public_cache_control


class CachePolicyTests(unittest.TestCase):
    def test_only_public_aggregate_routes_are_cacheable(self):
        self.assertIn("s-maxage=300", public_cache_control("/v1/public/market") or "")
        self.assertEqual(public_cache_control("/v1/public/insights/skill-salary-premiums"), PUBLIC_INSIGHT_CACHE_CONTROL)
        self.assertIsNone(public_cache_control("/v1/roles"))
        self.assertIsNone(public_cache_control("/v1/resume/upload"))
        self.assertIsNone(public_cache_control("/auth/verify"))

    def test_public_market_exposes_publication_timestamp(self):
        source = Path(__file__).with_name("api.py").read_text()
        self.assertIn("MAX(pr.published_at)", source)
        self.assertIn("AS publication_at", source)


if __name__ == "__main__":
    unittest.main()
