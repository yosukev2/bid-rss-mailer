from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import requests

from bid_rss_mailer.subscribers import build_subscriber_input, keyword_sets_to_json, parse_keyword_sets, validate_email
from bid_rss_mailer.storage import SQLiteStore

STOP_EVENT_TYPES = {
    "invoice.payment_failed",
    "customer.subscription.deleted",
}
STOP_SUBSCRIPTION_STATUSES = {
    "canceled",
    "paused",
    "past_due",
    "unpaid",
    "incomplete_expired",
}


@dataclass(frozen=True)
class StripeCheckoutResult:
    session_id: str
    checkout_url: str
    mock_mode: bool


@dataclass(frozen=True)
class StripeWebhookApplyResult:
    event_id: str
    event_type: str
    action: str
    email_norm: str | None
    status: str | None
    customer_id: str | None


def create_checkout_session(
    *,
    secret_key: str,
    price_id: str,
    customer_email: str,
    success_url: str,
    cancel_url: str,
    plan: str,
    keyword_sets: tuple[str, ...],
    mock_mode: bool,
    timeout_sec: int = 30,
) -> StripeCheckoutResult:
    if mock_mode:
        session_id = f"cs_test_mock_{uuid4().hex[:24]}"
        return StripeCheckoutResult(
            session_id=session_id,
            checkout_url=f"https://checkout.stripe.mock/c/pay/{session_id}",
            mock_mode=True,
        )

    response = requests.post(
        "https://api.stripe.com/v1/checkout/sessions",
        headers={"Authorization": f"Bearer {secret_key}"},
        data={
            "mode": "subscription",
            "customer_email": customer_email,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "metadata[plan]": plan,
            "metadata[keyword_sets]": ",".join(keyword_sets),
        },
        timeout=timeout_sec,
    )
    if response.status_code >= 400:
        detail = _extract_stripe_error_message(response.text)
        raise RuntimeError(
            f"Stripe API checkout create failed status={response.status_code} detail={detail}"
        )

    payload = response.json()
    session_id = str(payload.get("id") or "").strip()
    checkout_url = str(payload.get("url") or "").strip()
    if not session_id or not checkout_url:
        raise RuntimeError("Stripe checkout response is missing id/url")
    return StripeCheckoutResult(
        session_id=session_id,
        checkout_url=checkout_url,
        mock_mode=False,
    )


def build_test_signature_header(
    *,
    payload: bytes,
    webhook_secret: str,
    timestamp: int | None = None,
) -> str:
    signed_at = timestamp or int(time.time())
    digest = hmac.new(
        webhook_secret.encode("utf-8"),
        f"{signed_at}.{payload.decode('utf-8')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={signed_at},v1={digest}"


def verify_webhook_signature(
    *,
    payload: bytes,
    signature_header: str,
    webhook_secret: str,
    tolerance_sec: int = 300,
    now_unix: int | None = None,
) -> None:
    timestamp, signatures = _parse_signature_header(signature_header)
    current = now_unix or int(time.time())
    if abs(current - timestamp) > tolerance_sec:
        raise ValueError("Stripe signature timestamp is outside tolerance")
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise ValueError("Invalid Stripe webhook signature")


def apply_webhook_payload(
    *,
    store: SQLiteStore,
    payload: bytes,
    signature_header: str | None,
    webhook_secret: str | None,
    verify_signature: bool,
    default_plan: str,
    default_keyword_sets: tuple[str, ...],
    now: datetime | None = None,
) -> StripeWebhookApplyResult:
    if verify_signature:
        if not signature_header:
            raise ValueError("Stripe signature header is required")
        if not webhook_secret:
            raise ValueError("STRIPE_WEBHOOK_SECRET is required")
        verify_webhook_signature(
            payload=payload,
            signature_header=signature_header,
            webhook_secret=webhook_secret,
        )

    event = json.loads(payload.decode("utf-8"))
    if not isinstance(event, dict):
        raise ValueError("Stripe webhook payload must be JSON object")
    event_type = str(event.get("type") or "").strip()
    event_id = str(event.get("id") or "").strip()
    if not event_type:
        raise ValueError("Stripe webhook payload is missing event type")

    data = event.get("data")
    if not isinstance(data, dict):
        raise ValueError("Stripe webhook payload is missing data object")
    obj = data.get("object")
    if not isinstance(obj, dict):
        raise ValueError("Stripe webhook payload is missing data.object")

    metadata = obj.get("metadata")
    metadata_map = metadata if isinstance(metadata, dict) else {}
    customer_id = _extract_customer_id(obj)
    email = _extract_email(obj)
    if not email and customer_id:
        email = store.email_norm_by_stripe_customer(customer_id)

    plan = str(metadata_map.get("plan") or default_plan).strip() or default_plan
    keyword_sets_raw = str(metadata_map.get("keyword_sets") or ",".join(default_keyword_sets))
    keyword_sets = parse_keyword_sets(keyword_sets_raw)
    now_iso = (now or datetime.now(timezone.utc)).isoformat()

    if event_type == "checkout.session.completed":
        if not email:
            raise ValueError("checkout.session.completed is missing customer email")
        subscriber = build_subscriber_input(
            email=email,
            status="active",
            plan=plan,
            keyword_sets=",".join(keyword_sets),
        )
        store.upsert_subscriber(
            email=subscriber.email,
            email_norm=subscriber.email_norm,
            status=subscriber.status,
            plan=subscriber.plan,
            keyword_sets=keyword_sets_to_json(subscriber.keyword_sets),
            now_iso=now_iso,
        )
        if customer_id:
            store.upsert_stripe_customer(
                customer_id=customer_id,
                email_norm=subscriber.email_norm,
                now_iso=now_iso,
            )
        return StripeWebhookApplyResult(
            event_id=event_id,
            event_type=event_type,
            action="activated",
            email_norm=subscriber.email_norm,
            status="active",
            customer_id=customer_id,
        )

    should_stop = event_type in STOP_EVENT_TYPES
    if event_type == "customer.subscription.updated":
        status = str(obj.get("status") or "").strip().lower()
        should_stop = status in STOP_SUBSCRIPTION_STATUSES

    if should_stop:
        if not email:
            raise ValueError(f"{event_type} is missing email and customer mapping")
        email_norm = validate_email(email)
        updated = store.update_subscriber_status(
            email_norm=email_norm,
            status="stopped",
            now_iso=now_iso,
        )
        if not updated:
            subscriber = build_subscriber_input(
                email=email_norm,
                status="stopped",
                plan=plan,
                keyword_sets=",".join(keyword_sets),
            )
            store.upsert_subscriber(
                email=subscriber.email,
                email_norm=subscriber.email_norm,
                status=subscriber.status,
                plan=subscriber.plan,
                keyword_sets=keyword_sets_to_json(subscriber.keyword_sets),
                now_iso=now_iso,
            )
        if customer_id:
            store.upsert_stripe_customer(
                customer_id=customer_id,
                email_norm=email_norm,
                now_iso=now_iso,
            )
        return StripeWebhookApplyResult(
            event_id=event_id,
            event_type=event_type,
            action="stopped",
            email_norm=email_norm,
            status="stopped",
            customer_id=customer_id,
        )

    return StripeWebhookApplyResult(
        event_id=event_id,
        event_type=event_type,
        action="ignored",
        email_norm=validate_email(email) if email else None,
        status=None,
        customer_id=customer_id,
    )


def _extract_stripe_error_message(raw_text: str) -> str:
    try:
        payload = json.loads(raw_text)
    except Exception:  # noqa: BLE001
        return raw_text.strip()[:240]
    if not isinstance(payload, dict):
        return str(payload)
    error = payload.get("error")
    if not isinstance(error, dict):
        return str(payload)
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return str(error)


def _parse_signature_header(signature_header: str) -> tuple[int, tuple[str, ...]]:
    timestamp: int | None = None
    signatures: list[str] = []
    parts = [part.strip() for part in signature_header.split(",") if part.strip()]
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise ValueError("Stripe signature timestamp is invalid") from exc
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None:
        raise ValueError("Stripe signature header is missing timestamp")
    if not signatures:
        raise ValueError("Stripe signature header is missing v1 signature")
    return timestamp, tuple(signatures)


def _extract_email(obj: dict[str, Any]) -> str | None:
    direct_keys = (
        "customer_email",
        "receipt_email",
        "email",
    )
    for key in direct_keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    customer_details = obj.get("customer_details")
    if isinstance(customer_details, dict):
        value = customer_details.get("email")
        if isinstance(value, str) and value.strip():
            return value.strip()
    billing_details = obj.get("billing_details")
    if isinstance(billing_details, dict):
        value = billing_details.get("email")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_customer_id(obj: dict[str, Any]) -> str | None:
    customer = obj.get("customer")
    if isinstance(customer, str) and customer.strip():
        return customer.strip()
    return None
