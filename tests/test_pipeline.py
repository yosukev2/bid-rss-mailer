from datetime import datetime, timezone

from bid_rss_mailer.config import KeywordSetConfig
from bid_rss_mailer.domain import FeedItem, ScoredItem, StoredScoredItem
from bid_rss_mailer.pipeline import _apply_total_limit, _filter_new_records, _resolve_recipients
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


def test_apply_total_limit_caps_all_sets() -> None:
    keyword_sets = [
        KeywordSetConfig(
            id="set-a",
            name="A",
            enabled=True,
            min_required_matches=2,
            required=("a", "b"),
            boost=("c",),
            exclude=("d",),
            exclude_exceptions=(),
            top_n=10,
        ),
        KeywordSetConfig(
            id="set-b",
            name="B",
            enabled=True,
            min_required_matches=2,
            required=("a", "b"),
            boost=("c",),
            exclude=("d",),
            exclude_exceptions=(),
            top_n=10,
        ),
    ]

    def _record(item_id: int, score: int) -> StoredScoredItem:
        return StoredScoredItem(
            item_id=item_id,
            scored_item=ScoredItem(
                keyword_set_id="set-a",
                keyword_set_name="A",
                item=_item(f"https://example.com/{item_id}"),
                score=score,
                required_matches=("a", "b"),
                boost_matches=(),
            ),
        )

    selected = {
        "set-a": [_record(1, 20), _record(2, 19), _record(3, 18)],
        "set-b": [_record(4, 17), _record(5, 16)],
    }
    limited = _apply_total_limit(selected_by_set=selected, keyword_sets=keyword_sets, max_total_items=4)
    assert len(limited["set-a"]) == 3
    assert len(limited["set-b"]) == 1


def test_resolve_recipients_includes_admin_copy() -> None:
    recipients = _resolve_recipients(
        active_subscribers=["a@example.com", "b@example.com"],
        admin_email="admin@example.com",
        send_admin_copy=True,
    )
    assert recipients == ["a@example.com", "b@example.com", "admin@example.com"]

