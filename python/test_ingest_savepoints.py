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

    @patch("ingest_jobs.time.sleep")
    @patch("ingest_jobs.ingest_job")
    def test_deadlock_retries_the_row(self, ingest_job_mock, sleep_mock):
        deadlock = RuntimeError("deadlock detected")
        deadlock.pgcode = "40P01"
        ingest_job_mock.side_effect = [deadlock, True]

        self.assertTrue(_ingest_job_with_savepoint(self.cursor, self.job))

        self.assertEqual(ingest_job_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0.25)
        self.assertEqual(
            self.cursor.execute.call_args_list,
            [
                unittest.mock.call("SAVEPOINT ingest_one"),
                unittest.mock.call("ROLLBACK TO SAVEPOINT ingest_one"),
                unittest.mock.call("RELEASE SAVEPOINT ingest_one"),
                unittest.mock.call("SAVEPOINT ingest_one"),
                unittest.mock.call("RELEASE SAVEPOINT ingest_one"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
