import pytest

from bid_rss_mailer.subscribers import (
    build_subscriber_input,
    keyword_sets_from_json,
    keyword_sets_to_json,
    validate_email,
)


def test_validate_email_rejects_invalid_value() -> None:
    with pytest.raises(ValueError):
        validate_email("not-an-email")


def test_build_subscriber_input_parses_keyword_sets() -> None:
    subscriber = build_subscriber_input(
        email="User@example.com",
        status="active",
        plan="manual",
        keyword_sets="set-a-it-ops-cloud,set-c-research-study",
    )
    assert subscriber.email_norm == "user@example.com"
    assert subscriber.keyword_sets == ("set-a-it-ops-cloud", "set-c-research-study")


def test_keyword_sets_json_roundtrip() -> None:
    raw = keyword_sets_to_json(("all",))
    parsed = keyword_sets_from_json(raw)
    assert parsed == ("all",)
