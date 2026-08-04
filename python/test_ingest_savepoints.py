#!/usr/bin/env python3
"""Regression checks for bounded per-job ingestion savepoints."""
import unittest
from unittest.mock import Mock, patch

from ingest_jobs import RawJob, _ingest_job_with_savepoint


class IngestSavepointTests(unittest.TestCase):
    def setUp(self):
        self.cursor = Mock()
        self.job = RawJob(
            source="greenhouse",
            source_id="probe",
            title="Data Engineer",
            company="Probe",
        )

    @patch("ingest_jobs.ingest_job", return_value=True)
    def test_success_releases_savepoint(self, _ingest_job):
        self.assertTrue(_ingest_job_with_savepoint(self.cursor, self.job))
        self.assertEqual(
            self.cursor.execute.call_args_list,
            [
                unittest.mock.call("SAVEPOINT ingest_one"),
                unittest.mock.call("RELEASE SAVEPOINT ingest_one"),
            ],
        )

    @patch("ingest_jobs.ingest_job", side_effect=ValueError("bad row"))
    def test_failure_rolls_back_and_releases_savepoint(self, _ingest_job):
        with self.assertRaisesRegex(ValueError, "bad row"):
            _ingest_job_with_savepoint(self.cursor, self.job)
        self.assertEqual(
            self.cursor.execute.call_args_list,
            [
                unittest.mock.call("SAVEPOINT ingest_one"),
                unittest.mock.call("ROLLBACK TO SAVEPOINT ingest_one"),
                unittest.mock.call("RELEASE SAVEPOINT ingest_one"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
