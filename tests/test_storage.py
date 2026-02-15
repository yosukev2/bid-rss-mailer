from datetime import datetime, timezone

from bid_rss_mailer.domain import FeedItem, ScoredItem, StoredScoredItem
from bid_rss_mailer.storage import SQLiteStore


def _feed_item(url: str) -> FeedItem:
    now = datetime.now(timezone.utc)
    return FeedItem(
        source_id="source-1",
        organization="Org",
        title="title",
        url=url,
        published_at=now,
        fetched_at=now,
        description="",
        deadline_at=None,
    )


def test_upsert_item_deduplicates_by_stable_url_key(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        first_id = store.upsert_item(_feed_item("https://example.com/a?x=1&utm_source=aa"))
        second_id = store.upsert_item(_feed_item("https://example.com/a?x=1"))
        assert first_id == second_id
    finally:
        store.close()


def test_record_deliveries_blocks_resend(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        item_id = store.upsert_item(_feed_item("https://example.com/a"))
        record = StoredScoredItem(
            item_id=item_id,
            scored_item=ScoredItem(
                keyword_set_id="set-a",
                keyword_set_name="A",
                item=_feed_item("https://example.com/a"),
                score=10,
                required_matches=("運用", "保守"),
                boost_matches=(),
            ),
        )
        store.record_deliveries("run-1", "set-a", [record])
        delivered = store.delivered_item_ids("set-a", [item_id])
        assert item_id in delivered
    finally:
        store.close()

