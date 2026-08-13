from unittest.mock import MagicMock

from python.notify_indexnow import CURATED_PATHS, MAX_URLS, collect_urls, mark_queue


def test_curated_inventory_contains_answer_and_market_pages():
    assert "/market" in CURATED_PATHS
    assert "/answers" in CURATED_PATHS
    assert "/answers/remote-job-market" in CURATED_PATHS
    assert MAX_URLS == 10_000


def test_collect_urls_prioritizes_lifecycle_queue():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [{"url": "https://www.landerjob.com/jobs/opening/example-1"}],
        [{"path": "/companies/acme"}],
    ]
    urls, queued = collect_urls(cursor, 100)
    assert urls[0] == "https://www.landerjob.com/jobs/opening/example-1"
    assert queued == ["https://www.landerjob.com/jobs/opening/example-1"]
    assert "https://www.landerjob.com/companies/acme" in urls


def test_successful_submission_marks_only_selected_queue_urls():
    cursor = MagicMock()
    mark_queue(cursor, ["https://www.landerjob.com/market"])
    sql, params = cursor.execute.call_args.args
    assert "indexnow_sent_at=now()" in sql
    assert params == (["https://www.landerjob.com/market"],)
