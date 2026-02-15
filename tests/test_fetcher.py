from datetime import datetime, timezone

from bid_rss_mailer.config import SourceConfig
from bid_rss_mailer.fetcher import fetch_source


class _DummyResponse:
    def __init__(self) -> None:
        self.content = b"<xml/>"

    def raise_for_status(self) -> None:
        return None


class _DummySession:
    def get(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _DummyResponse()


class _ParsedFeed:
    bozo = False
    entries = [{"title": "title", "link": "https://example.com/item"}]


def test_fetch_source_fallbacks_published_at_to_fetched_at(monkeypatch) -> None:
    monkeypatch.setattr("bid_rss_mailer.fetcher.feedparser.parse", lambda _: _ParsedFeed())
    source = SourceConfig(
        id="source-1",
        name="source",
        organization="org",
        url="https://example.com/feed.xml",
        enabled=True,
        timeout_sec=10,
        retries=0,
    )
    items, failure = fetch_source(_DummySession(), source)

    assert failure is None
    assert len(items) == 1
    assert items[0].published_at is not None
    assert isinstance(items[0].published_at, datetime)
    assert items[0].published_at.tzinfo == timezone.utc
    assert items[0].published_at == items[0].fetched_at

