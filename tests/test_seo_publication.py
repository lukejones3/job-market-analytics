import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from python.notify_google_indexing import main as notify_main
from python.publish_snapshot import _slug


class SeoPublicationTest(unittest.TestCase):
    def test_job_title_slug_matches_public_url_contract(self):
        self.assertEqual(_slug("Senior Data Engineer — AI/ML"), "senior-data-engineer-ai-ml")
        self.assertEqual(len(_slug("x" * 200)), 80)

    def test_indexing_notifier_is_safe_without_credentials(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            notify_main()
        self.assertIn("skipped", output.getvalue())


if __name__ == "__main__":
    unittest.main()
