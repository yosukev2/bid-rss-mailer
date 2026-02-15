from __future__ import annotations

import json
import re
from dataclasses import dataclass

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_SUBSCRIBER_STATUS = {"active", "paused", "stopped"}


@dataclass(frozen=True)
class SubscriberInput:
    email: str
    email_norm: str
    status: str
    plan: str
    keyword_sets: tuple[str, ...]


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if not normalized or not EMAIL_PATTERN.match(normalized):
        raise ValueError(f"invalid email: {email}")
    return normalized


def validate_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized not in ALLOWED_SUBSCRIBER_STATUS:
        raise ValueError(f"invalid subscriber status: {status}")
    return normalized


def parse_keyword_sets(raw: str) -> tuple[str, ...]:
    value = (raw or "").strip()
    if not value:
        return ("all",)
    parts = [item.strip() for item in value.split(",") if item.strip()]
    if not parts:
        return ("all",)
    # Keep deterministic order and remove duplicates.
    return tuple(dict.fromkeys(parts))


def keyword_sets_to_json(keyword_sets: tuple[str, ...]) -> str:
    return json.dumps(list(keyword_sets), ensure_ascii=False)


def keyword_sets_from_json(raw: str) -> tuple[str, ...]:
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return ("all",)
    if not isinstance(payload, list):
        return ("all",)
    values: list[str] = []
    for item in payload:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
    if not values:
        return ("all",)
    return tuple(values)


def build_subscriber_input(
    *,
    email: str,
    status: str,
    plan: str,
    keyword_sets: str,
) -> SubscriberInput:
    email_norm = validate_email(email)
    return SubscriberInput(
        email=email.strip(),
        email_norm=email_norm,
        status=validate_status(status),
        plan=(plan or "manual").strip() or "manual",
        keyword_sets=parse_keyword_sets(keyword_sets),
    )
