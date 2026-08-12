import unittest

from unittest.mock import patch

from icims_harvest import _fetch_icims_description, _icims_job_url, _parse_icims_job_ids


class IcimsParserTests(unittest.TestCase):
    def test_parse_current_slugged_icims_job_url(self):
        html = """
        <div class="iCIMS_JobsTable">
          <a href="https://careers-example.icims.com/jobs/4812/data-engineer/job?in_iframe=1">
            <span>Title</span> Data Engineer
          </a>
          <span class="location">Seattle, WA</span>
        </div>
        """

        self.assertEqual(_parse_icims_job_ids(html, "careers-example"), [
            ("4812", "Title Data Engineer", "Seattle, WA")
        ])

    def test_current_card_ignores_accessibility_label(self):
        html = """
        <li class="iCIMS_JobCardItem">
          <span class="sr-only field-label">Job Locations</span><span>US-WA-Seattle</span>
          <a href="/jobs/20754/data-scientist/job?in_iframe=1">
            <span class="sr-only field-label">Requisition Title</span>
            <h3>Data Scientist</h3>
          </a>
        </li>
        """

        self.assertEqual(_parse_icims_job_ids(html, "example"), [
            ("20754", "Data Scientist", "US-WA-Seattle")
        ])


    def test_parse_legacy_icims_job_url_and_deduplicate(self):
        html = """
        <div><a href="/jobs/123/job">Data Analyst</a></div>
        <div><a href="/jobs/123/job?in_iframe=1">Data Analyst</a></div>
        """

        self.assertEqual(_parse_icims_job_ids(html, "example"), [
            ("123", "Data Analyst", "")
        ])

    def test_preserves_slugged_url_and_reads_json_ld_date(self):
        search = '<a href="/jobs/4812/data-engineer/job?in_iframe=1">Data Engineer</a>'
        url = _icims_job_url(search, "https://careers-example.icims.com", "4812")
        self.assertEqual(
            url,
            "https://careers-example.icims.com/jobs/4812/data-engineer/job?in_iframe=1",
        )
        detail = """
        <html><head>
          <link rel="canonical" href="/jobs/4812/data-engineer/job" />
          <script type="application/ld+json">
          {"@context":"https://schema.org","@type":"JobPosting",
           "datePosted":"2026-08-10","description":"<p>Build systems.</p>"}
          </script>
        </head><body><div class="location">Seattle, WA</div></body></html>
        """
        with patch("icims_harvest._get_html", return_value=detail):
            desc, location, posted, canonical = _fetch_icims_description(
                "https://careers-example.icims.com", "4812", url
            )
        self.assertEqual(posted, "2026-08-10")
        self.assertEqual(canonical, "https://careers-example.icims.com/jobs/4812/data-engineer/job")
        self.assertIn("Build systems", desc)
        self.assertEqual(location, "Seattle, WA")


if __name__ == "__main__":
    unittest.main()
