from unittest.mock import MagicMock, patch

import pytest

from python.billing import process_event, qualifies_for_pro, subscription_id


def test_subscription_access_requires_current_state_and_the_lander_price():
    sub = {'status': 'active', 'items': {'data': [{'price': {'id': 'price_lander'}}]}}
    assert qualifies_for_pro(sub, 'price_lander')
    assert not qualifies_for_pro(sub, 'different')
    for state in ['canceled', 'past_due', 'unpaid', 'incomplete']:
        assert not qualifies_for_pro({**sub, 'status': state}, 'price_lander')


def test_invoice_subscription_supports_both_stripe_object_shapes():
    for obj in [{'subscription': 'sub_1'}, {'parent': {'subscription_details': {'subscription': 'sub_1'}}}]:
        assert subscription_id({'type': 'invoice.paid', 'data': {'object': obj}}) == 'sub_1'


def test_duplicate_delivery_never_changes_account_state():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = {'processed_at': 'already'}
    event = {'id': 'evt_1', 'type': 'checkout.session.completed', 'data': {'object': {'subscription': 'sub_1'}}}
    with patch('python.billing.stripe.Subscription.retrieve', return_value={'customer': 'cus_1'}):
        assert process_event(event, lambda: conn, lambda c: None, 'price_1')['status'] == 'duplicate'
    assert not any('UPDATE api_keys' in call.args[0] for call in cur.execute.call_args_list)


def test_failed_database_work_rolls_back_and_propagates_for_stripe_retry():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = RuntimeError('database unavailable')
    event = {'id': 'evt_1', 'type': 'checkout.session.completed', 'data': {'object': {'subscription': 'sub_1'}}}
    release = MagicMock()
    with patch('python.billing.stripe.Subscription.retrieve', return_value={'customer': 'cus_1'}):
        with pytest.raises(RuntimeError):
            process_event(event, lambda: conn, release, 'price_1')
    conn.rollback.assert_called_once()
    release.assert_called_once_with(conn)
