import json
from datetime import datetime, timedelta, timezone

import pytest
import morning_report as report


def test_late_pipeline_completion_does_not_consume_tomorrows_central_brief():
    assert report.delivery_day(datetime(2026,10,31,2,tzinfo=timezone.utc)) == '2026-10-30'
    assert report.delivery_day(datetime(2026,10,31,13,30,tzinfo=timezone.utc)) == '2026-10-31'


def test_stale_publication_cannot_be_reported_healthy():
    now = datetime.now(timezone.utc)
    assert report.report_state([], (now-timedelta(days=6),), {'state':'success'}, now) == 'Needs attention'
    assert report.report_state([], None, {'state':'success'}, now) == 'Needs attention'
    assert report.report_state([], (now,), {'state':'failed'}, now) == 'Needs attention'
    assert report.report_state([], (now,), {'state':'running'}, now) == 'In progress'
    assert report.report_state([], (now,), {'state':'success'}, now) == 'Healthy'


def test_missing_credentials_fail_delivery(monkeypatch):
    monkeypatch.delenv('RESEND_API_KEY', raising=False)
    with pytest.raises(RuntimeError, match='not delivered'):
        report.send_email('body')


def test_report_retries_identical_body_and_does_not_duplicate_accepted_email(monkeypatch, tmp_path):
    monkeypatch.setenv('MORNING_REPORT_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr('sys.argv', ['morning_report'])
    bodies = iter(['first', 'second', 'third'])
    monkeypatch.setattr(report, 'build_report', lambda: next(bodies))
    calls = []
    def send(body, key):
        calls.append((body, key))
        if len(calls) == 1:
            raise TimeoutError('provider response lost')
        return 'delivery-id'
    monkeypatch.setattr(report, 'send_email', send)
    with pytest.raises(TimeoutError):
        report.main()
    report.main()
    report.main()
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][0] == 'first'
    ledger = next(tmp_path.glob('morning-report-*.json'))
    assert json.loads(ledger.read_text())['delivery_id'] == 'delivery-id'


def test_metrics_failure_still_sends_operational_warning(monkeypatch, tmp_path):
    monkeypatch.setenv('MORNING_REPORT_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr('sys.argv', ['morning_report'])
    def broken():
        raise RuntimeError('sensitive connection details')
    monkeypatch.setattr(report, 'build_report', broken)
    sent = []
    monkeypatch.setattr(report, 'send_email', lambda body, key: sent.append(body) or 'id')
    report.main()
    assert 'Needs attention' in sent[0]
    assert 'sensitive connection details' not in sent[0]
