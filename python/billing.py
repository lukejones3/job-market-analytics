"""Idempotent Stripe subscription state, owned by a stable Lander account.

No email is sent in the webhook transaction. Stripe retries unsuccessful work;
out-of-order events reconcile against Stripe's current subscription object.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

import stripe
from psycopg2.extras import RealDictCursor

SUPPORTED_EVENTS = frozenset({
    'checkout.session.completed', 'customer.subscription.created',
    'customer.subscription.updated', 'customer.subscription.deleted',
    'invoice.paid', 'invoice.payment_failed',
})
PRO_STATUSES = frozenset({'active', 'trialing'})


def object_id(value) -> str | None:
    return value.get('id') if isinstance(value, dict) else str(value) if value else None


def subscription_id(event: dict) -> str | None:
    obj = event['data']['object']
    if event['type'].startswith('customer.subscription.'):
        return object_id(obj)
    return object_id(obj.get('subscription')) or object_id(
        (obj.get('parent') or {}).get('subscription_details', {}).get('subscription')
    )


def qualifies_for_pro(subscription: dict, price_id: str) -> bool:
    return subscription.get('status') in PRO_STATUSES and any(
        object_id(item.get('price')) == price_id
        for item in (subscription.get('items') or {}).get('data', [])
    )


def process_event(event: dict, connect: Callable, release: Callable, price_id: str) -> dict:
    kind = event['type']
    if kind not in SUPPORTED_EVENTS:
        return {'status': 'ignored'}
    sub_id = subscription_id(event)
    if not sub_id:
        return {'status': 'ignored', 'reason': 'not a subscription event'}
    customer_id = object_id(event['data']['object'].get('customer'))
    if not customer_id:
        customer_id = object_id(stripe.Subscription.retrieve(sub_id).get('customer'))
    if not customer_id:
        raise ValueError('Subscription customer missing')
    conn = connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ('billing:' + customer_id,))
            cur.execute('SELECT processed_at FROM billing_events WHERE event_id=%s', (event['id'],))
            if cur.fetchone():
                conn.rollback()
                return {'status': 'duplicate'}
            # Fetch current state AFTER acquiring the customer lock: concurrent
            # deliveries must not commit snapshots taken before another event.
            subscription = stripe.Subscription.retrieve(sub_id)
            if object_id(subscription.get('customer')) != customer_id:
                raise ValueError('Subscription customer changed')
            cur.execute('SELECT key_id FROM billing_customers WHERE customer_id=%s', (customer_id,))
            account = cur.fetchone()
            reference = event['data']['object'].get('client_reference_id') if kind == 'checkout.session.completed' else None
            reference = reference or (subscription.get('metadata') or {}).get('lander_account_id')
            if not account and reference:
                cur.execute('SELECT key_id FROM api_keys WHERE key_id=%s AND active=true', (reference,))
                account = cur.fetchone()
            if not account:
                customer = stripe.Customer.retrieve(customer_id)
                email = (customer.get('email') or '').strip().lower()
                cur.execute('''SELECT ak.key_id FROM api_keys ak
                    LEFT JOIN free_signups fs ON fs.api_key_id=ak.key_id
                    WHERE lower(ak.client_email)=%s AND ak.active=true
                    ORDER BY (fs.api_key_id IS NOT NULL) DESC,ak.created_at DESC LIMIT 1''', (email,))
                account = cur.fetchone()
            if not account:
                raise ValueError('No active Lander account for subscription')
            key_id = account['key_id']
            cur.execute('''INSERT INTO billing_customers(customer_id,key_id) VALUES(%s,%s)
                ON CONFLICT(customer_id) DO NOTHING''', (customer_id, key_id))
            entitled = qualifies_for_pro(subscription, price_id)
            cur.execute('''INSERT INTO billing_subscriptions(subscription_id,customer_id,key_id,status,qualifies,updated_at)
                VALUES(%s,%s,%s,%s,%s,now()) ON CONFLICT(subscription_id) DO UPDATE SET
                  status=EXCLUDED.status,qualifies=EXCLUDED.qualifies,updated_at=now()''',
                (sub_id, customer_id, key_id, subscription['status'], entitled))
            cur.execute('''SELECT EXISTS(SELECT 1 FROM billing_subscriptions
                WHERE key_id=%s AND qualifies=true AND status IN ('active','trialing')) AS pro''', (key_id,))
            pro = bool(cur.fetchone()['pro'])
            cur.execute("UPDATE api_keys SET tier=%s WHERE key_id=%s", ('pro' if pro else 'free', key_id))
            if pro:
                cur.execute("""INSERT INTO product_lifecycle_events(user_id,event_name,dedupe_key)
                    VALUES(%s,'upgraded','first') ON CONFLICT DO NOTHING""", (key_id,))
            cur.execute('''UPDATE free_signups SET upgraded_to_paid_at=CASE WHEN %s
                THEN COALESCE(upgraded_to_paid_at,now()) ELSE NULL END WHERE api_key_id=%s''', (pro, key_id))
            cur.execute('''INSERT INTO billing_events(event_id,event_type,stripe_created_at,processed_at)
                VALUES(%s,%s,%s,now())''', (event['id'], kind,
                datetime.fromtimestamp(event['created'], timezone.utc)))
            conn.commit()
            return {'status': 'ok', 'tier': 'pro' if pro else 'free'}
    except Exception:
        conn.rollback()
        raise
    finally:
        release(conn)
