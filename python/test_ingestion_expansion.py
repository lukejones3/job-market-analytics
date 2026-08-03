#!/usr/bin/env python3
"""Dependency-light regression checks for ingestion expansion behavior."""
import unittest

from integrate_ats_candidates import _board_token
from role_taxonomy import SEARCH_TERMS
from validate_ats_candidates import _candidate_status
from coverage_ingest import _jsonld_objects, _location


class IngestionExpansionTests(unittest.TestCase):
    def test_small_target_employer_is_active(self):
        self.assertEqual(_candidate_status(1, 1), "active")
        self.assertEqual(_candidate_status(4, 1), "active")

    def test_non_target_and_unreachable_are_distinct(self):
        self.assertEqual(_candidate_status(3, 0), "no_data_jobs")
        self.assertEqual(_candidate_status(0, 0), "unreachable")

    def test_search_vocabulary_covers_non_family_aliases(self):
        for term in ("controller", "auditor", "copywriter", "customer success",
                     "solutions architect", "ux", "procurement"):
            self.assertIn(term, SEARCH_TERMS)

    def test_discovered_board_tokens_preserve_required_parts(self):
        self.assertEqual(_board_token("workday", "acme", "wd5/Careers"),
                         "acme/wd5/Careers")
        self.assertEqual(_board_token("eightfold", "acme", "acme.com"),
                         "acme/acme.com")
        self.assertIsNone(_board_token("eightfold", "acme", None))

    def test_nested_jsonld_job_postings_are_discovered(self):
        payload = {"@graph": [{"@type": "Organization"},
                              {"@type": "JobPosting", "title": "Data Analyst"}]}
        self.assertEqual([item["title"] for item in _jsonld_objects(payload)],
                         ["Data Analyst"])

    def test_jsonld_remote_location(self):
        self.assertEqual(_location({"jobLocationType": "TELECOMMUTE"}), "Remote")


if __name__ == "__main__":
    unittest.main()
