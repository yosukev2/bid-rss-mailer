from datetime import datetime, timedelta, timezone

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


def test_purge_older_than_removes_old_rows(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLiteStore(str(db_path))
    store.initialize()
    now = datetime(2026, 2, 15, tzinfo=timezone.utc)
    old = now - timedelta(days=40)
    try:
        old_item = FeedItem(
            source_id="source-1",
            organization="Org",
            title="old",
            url="https://example.com/old",
            published_at=old,
            fetched_at=old,
            description="",
            deadline_at=None,
        )
        new_item = FeedItem(
            source_id="source-1",
            organization="Org",
            title="new",
            url="https://example.com/new",
            published_at=now,
            fetched_at=now,
            description="",
            deadline_at=None,
        )
        old_id = store.upsert_item(old_item)
        new_id = store.upsert_item(new_item)

        old_record = StoredScoredItem(
            item_id=old_id,
            scored_item=ScoredItem(
                keyword_set_id="set-a",
                keyword_set_name="A",
                item=old_item,
                score=10,
                required_matches=("運用", "保守"),
                boost_matches=(),
            ),
        )
        new_record = StoredScoredItem(
            item_id=new_id,
            scored_item=ScoredItem(
                keyword_set_id="set-a",
                keyword_set_name="A",
                item=new_item,
                score=10,
                required_matches=("運用", "保守"),
                boost_matches=(),
            ),
        )
        store.record_deliveries("run-old", "set-a", [old_record], delivered_at=old)
        store.record_deliveries("run-new", "set-a", [new_record], delivered_at=now)

        store.purge_older_than(days=30, now=now)

        remaining_items = store.connection.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
        remaining_deliveries = store.connection.execute("SELECT COUNT(*) AS c FROM deliveries").fetchone()["c"]
        assert remaining_items == 1
        assert remaining_deliveries == 1
    finally:
        store.close()


def test_record_x_draft_and_has_x_draft_for_date(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        assert store.has_x_draft_for_date("2026-02-16") is False
        store.record_x_draft(
            post_date_jst="2026-02-16",
            generated_at="2026-02-16T00:00:00+00:00",
            top_n=5,
            item_count=2,
            lp_url="https://example.com/lp",
            content="content",
            overwrite=False,
        )
        assert store.has_x_draft_for_date("2026-02-16") is True
    finally:
        store.close()


def test_record_x_post_and_has_x_post_for_date(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        assert store.has_x_post_for_date("2026-02-16") is False
        store.record_x_post(
            post_date_jst="2026-02-16",
            posted_at="2026-02-16T01:00:00+00:00",
            mode="manual",
            status="manual_ready",
            response_id=None,
            response_body="ok",
            overwrite=False,
        )
        assert store.has_x_post_for_date("2026-02-16") is True
    finally:
        store.close()
