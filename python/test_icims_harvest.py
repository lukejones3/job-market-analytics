import unittest

from icims_harvest import _parse_icims_job_ids


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


if __name__ == "__main__":
    unittest.main()
