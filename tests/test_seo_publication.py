import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from python.notify_google_indexing import (
    MAX_DAILY_LIMIT,
    compact_and_fetch_pending,
    main as notify_main,
    sent_since_pacific_midnight,
)
from python.publish_snapshot import _slug


class SeoPublicationTest(unittest.TestCase):
    def test_job_title_slug_matches_public_url_contract(self):
        self.assertEqual(_slug("Senior Data Engineer — AI/ML"), "senior-data-engineer-ai-ml")
        self.assertEqual(_slug("Research & Développement"), "research-and-developpement")
        self.assertEqual(len(_slug("x" * 200)), 80)

    def test_indexing_notifier_is_safe_without_credentials(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            notify_main()
        self.assertIn("skipped", output.getvalue())

    def test_indexing_queue_compacts_and_prioritizes_deletions(self):
        cursor = MagicMock()
        cursor.rowcount = 12
        rows = [{"job_id": "1", "notification_type": "URL_DELETED"}]
        cursor.fetchall.return_value = rows

        compacted, selected = compact_and_fetch_pending(cursor, 200)

        self.assertEqual(compacted, 12)
        self.assertEqual(selected, rows)
        delete_sql = cursor.execute.call_args_list[0].args[0]
        select_sql = cursor.execute.call_args_list[1].args[0]
        self.assertIn("stale.url = newer.url", delete_sql)
        self.assertIn("notification_type='URL_DELETED'", select_sql)
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (200,))

    def test_indexing_notifier_source_stops_after_quota_exhaustion(self):
        source = (Path(__file__).parents[1] / "python" / "notify_google_indexing.py").read_text()
        self.assertIn('response.status_code == 429', source)
        self.assertIn('"RESOURCE_EXHAUSTED" in response.text', source)
        self.assertRegex(source, r"quota_exhausted = True\s+break")
        quota_branch = source[source.index("if response.status_code == 429"):source.index("quota_exhausted = True")]
        self.assertNotIn("attempts=attempts+1", quota_branch)

    def test_google_quota_day_uses_pacific_midnight(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"sent": 187}

        self.assertEqual(sent_since_pacific_midnight(cursor), 187)
        sql = cursor.execute.call_args.args[0]
        self.assertIn("America/Los_Angeles", sql)

    def test_requested_daily_quota_is_supported(self):
        self.assertEqual(MAX_DAILY_LIMIT, 10_000)
        dag = (Path(__file__).parents[1] / "airflow" / "dags" / "lander_pipeline.py").read_text()
        self.assertIn("notify_google_indexing.py --limit 10000", dag)


if __name__ == "__main__":
    unittest.main()
