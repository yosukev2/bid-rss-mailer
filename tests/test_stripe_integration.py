from __future__ import annotations

import json

import pytest

from bid_rss_mailer.storage import SQLiteStore
from bid_rss_mailer.stripe_integration import (
    apply_webhook_payload,
    build_test_signature_header,
    create_checkout_session,
    verify_webhook_signature,
)


def test_create_checkout_session_mock_mode_returns_url() -> None:
    result = create_checkout_session(
        secret_key="sk_test_dummy",
        price_id="price_test_dummy",
        customer_email="user@example.com",
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
        plan="stripe-monthly",
        keyword_sets=("all",),
        mock_mode=True,
    )
    assert result.mock_mode is True
    assert result.session_id.startswith("cs_test_mock_")
    assert result.checkout_url.startswith("https://checkout.stripe.mock/")


def test_verify_webhook_signature_rejects_invalid_signature() -> None:
    payload = b'{"id":"ev_test","type":"checkout.session.completed","data":{"object":{}}}'
    signature = build_test_signature_header(payload=payload, webhook_secret="whsec_ok")
    verify_webhook_signature(
        payload=payload,
        signature_header=signature,
        webhook_secret="whsec_ok",
    )
    with pytest.raises(ValueError):
        verify_webhook_signature(
            payload=payload,
            signature_header=signature,
            webhook_secret="whsec_bad",
        )


def test_apply_checkout_event_activates_subscriber_and_maps_customer(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        event = {
            "id": "evt_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_123",
                    "customer_email": "Buyer@example.com",
                    "metadata": {
                        "plan": "stripe-monthly",
                        "keyword_sets": "all",
                    },
                }
            },
        }
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        signature = build_test_signature_header(payload=payload, webhook_secret="whsec_test")
        result = apply_webhook_payload(
            store=store,
            payload=payload,
            signature_header=signature,
            webhook_secret="whsec_test",
            verify_signature=True,
            default_plan="stripe-monthly",
            default_keyword_sets=("all",),
        )
        assert result.action == "activated"
        rows = store.list_subscribers(status="active")
        assert len(rows) == 1
        assert rows[0]["email_norm"] == "buyer@example.com"
        assert store.email_norm_by_stripe_customer("cus_123") == "buyer@example.com"
    finally:
        store.close()


def test_apply_payment_failed_without_email_uses_customer_mapping(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        store.upsert_subscriber(
            email="buyer@example.com",
            email_norm="buyer@example.com",
            status="active",
            plan="stripe-monthly",
            keyword_sets='["all"]',
            now_iso="2026-02-16T00:00:00+00:00",
        )
        store.upsert_stripe_customer(
            customer_id="cus_abc",
            email_norm="buyer@example.com",
            now_iso="2026-02-16T00:00:00+00:00",
        )
        event = {
            "id": "evt_failed",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "customer": "cus_abc",
                }
            },
        }
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        signature = build_test_signature_header(payload=payload, webhook_secret="whsec_test")
        result = apply_webhook_payload(
            store=store,
            payload=payload,
            signature_header=signature,
            webhook_secret="whsec_test",
            verify_signature=True,
            default_plan="stripe-monthly",
            default_keyword_sets=("all",),
        )
        assert result.action == "stopped"
        rows = store.list_subscribers(status="stopped")
        assert len(rows) == 1
        assert rows[0]["email_norm"] == "buyer@example.com"
    finally:
        store.close()


def test_apply_webhook_payload_ignores_unhandled_event(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        event = {
            "id": "evt_ignored",
            "type": "invoice.created",
            "data": {
                "object": {
                    "customer_email": "ignored@example.com",
                }
            },
        }
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        result = apply_webhook_payload(
            store=store,
            payload=payload,
            signature_header=None,
            webhook_secret=None,
            verify_signature=False,
            default_plan="stripe-monthly",
            default_keyword_sets=("all",),
        )
        assert result.action == "ignored"
        assert store.list_subscribers() == []
    finally:
        store.close()
