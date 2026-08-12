from python.validate_public_source_urls import classify_response


def test_hard_not_found_and_closed_page_are_dead():
    assert classify_response(404, "")[0] == "dead"
    assert classify_response(410, "")[0] == "dead"
    assert classify_response(200, "This job is no longer available.")[0] == "dead"
    assert classify_response(200, "This requisition has been filled.")[0] == "dead"


def test_vendor_failures_and_bot_defenses_fail_open():
    for status in (401, 403, 429, 500, 503):
        assert classify_response(status, "error")[0] == "inconclusive"


def test_healthy_job_page_is_alive():
    assert classify_response(200, "Apply now for this software engineering role")[0] == "alive"
