from datetime import datetime, timezone

from bid_rss_mailer.config import KeywordSetConfig
from bid_rss_mailer.domain import FeedItem, ScoredItem, StoredScoredItem
from bid_rss_mailer.mailer import SmtpConfig, build_digest_body, send_text_email


class _FlakySMTP:
    attempts = 0

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # type: ignore[no-untyped-def]
        return False

    def ehlo(self) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        return None

    def send_message(self, message) -> None:  # type: ignore[no-untyped-def]
        _FlakySMTP.attempts += 1
        if _FlakySMTP.attempts == 1:
            raise OSError("temporary network error")


def test_send_text_email_retries_once(monkeypatch) -> None:
    _FlakySMTP.attempts = 0
    monkeypatch.setattr("bid_rss_mailer.mailer.smtplib.SMTP", _FlakySMTP)

    smtp_config = SmtpConfig(
        host="127.0.0.1",
        port=1025,
        user="",
        password="",
        from_address="noreply@example.local",
        starttls=False,
        use_ssl=False,
    )

    send_text_email(
        smtp_config=smtp_config,
        to_address="admin@example.local",
        subject="subject",
        body="body",
        max_attempts=3,
        retry_wait_sec=0,
    )

    assert _FlakySMTP.attempts == 2


def test_build_digest_body_includes_footer() -> None:
    now = datetime(2026, 2, 16, tzinfo=timezone.utc)
    keyword_sets = [
        KeywordSetConfig(
            id="set-a",
            name="A",
            enabled=True,
            min_required_matches=2,
            required=("運用", "保守"),
            boost=("クラウド",),
            exclude=("工事",),
            exclude_exceptions=(),
            top_n=10,
        )
    ]
    item = FeedItem(
        source_id="source-1",
        organization="Org",
        title="運用保守業務",
        url="https://example.com/a",
        published_at=now,
        fetched_at=now,
        description="",
        deadline_at=None,
    )
    record = StoredScoredItem(
        item_id=1,
        scored_item=ScoredItem(
            keyword_set_id="set-a",
            keyword_set_name="A",
            item=item,
            score=23,
            required_matches=("運用", "保守"),
            boost_matches=("クラウド",),
        ),
    )
    body = build_digest_body(
        now_jst=now,
        keyword_sets=keyword_sets,
        selected_by_set={"set-a": [record]},
        failures=[],
        unsubscribe_contact="support@example.com",
    )
    assert "免責:" in body
    assert "配信停止: support@example.com" in body

