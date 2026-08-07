"""Regression tests for ATS employment-type normalization."""

import unittest

from ingest_jobs import _normalize_employment_type


class EmploymentTypeNormalizationTests(unittest.TestCase):
    def test_normalizes_supported_explicit_ats_values(self):
        cases = [
        ("FullTime", "full-time"),
        ("FULL_TIME", "full-time"),
        ("full-time", "full-time"),
        ("PartTime", "part-time"),
        ("CONTRACTOR", "contract"),
        ("Temporary", "temporary"),
        ("Intern", "internship"),
        ("PER_DIEM", "per-diem"),
        ("Volunteer", "volunteer"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(_normalize_employment_type(raw), expected)

    def test_does_not_invent_employment_type(self):
        for raw in [None, "", "Regular", "Permanent", "Unknown"]:
            with self.subTest(raw=raw):
                self.assertIsNone(_normalize_employment_type(raw))
