from datetime import datetime, timezone

from bid_rss_mailer.config import KeywordSetConfig
from bid_rss_mailer.domain import FeedItem, ScoredItem
from bid_rss_mailer.pipeline import _filter_new_records
from bid_rss_mailer.storage import SQLiteStore


def _item(url: str) -> FeedItem:
    now = datetime.now(timezone.utc)
    return FeedItem(
        source_id="source-1",
        organization="org",
        title="title",
        url=url,
        published_at=now,
        fetched_at=now,
        description="",
        deadline_at=None,
    )


def test_filter_new_records_deduplicates_same_item_id(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "app.db"))
    store.initialize()
    try:
        item_id = store.upsert_item(_item("https://example.com/a?x=1&utm_source=x"))
        keyword_set = KeywordSetConfig(
            id="set-a",
            name="A",
            enabled=True,
            min_required_matches=2,
            required=("a", "b"),
            boost=("c",),
            exclude=("d",),
            exclude_exceptions=(),
            top_n=10,
        )
        scored = {
            "set-a": [
                ScoredItem(
                    keyword_set_id="set-a",
                    keyword_set_name="A",
                    item=_item("https://example.com/a?x=1&utm_source=x"),
                    score=20,
                    required_matches=("a", "b"),
                    boost_matches=(),
                ),
                ScoredItem(
                    keyword_set_id="set-a",
                    keyword_set_name="A",
                    item=_item("https://example.com/a?x=1"),
                    score=18,
                    required_matches=("a", "b"),
                    boost_matches=(),
                ),
            ]
        }
        item_ids_by_url = {
            "https://example.com/a?x=1&utm_source=x": item_id,
            "https://example.com/a?x=1": item_id,
        }

        selected = _filter_new_records(store, keyword_set, scored, item_ids_by_url)
        assert len(selected) == 1
        assert selected[0].item_id == item_id
    finally:
        store.close()

