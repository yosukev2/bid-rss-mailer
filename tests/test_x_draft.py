from datetime import datetime, timezone
from pathlib import Path

from bid_rss_mailer.domain import FeedItem, ScoredItem, StoredScoredItem
from bid_rss_mailer.storage import SQLiteStore
from bid_rss_mailer.x_draft import XDraftCandidate, build_x_post_content, generate_x_draft


def _item(url: str, title: str = "title", organization: str = "org") -> FeedItem:
    now = datetime(2026, 2, 16, 0, 10, tzinfo=timezone.utc)
    return FeedItem(
        source_id="source-1",
        organization=organization,
        title=title,
        url=url,
        published_at=now,
        fetched_at=now,
        description="",
        deadline_at=None,
    )


def test_build_x_post_content_includes_lp_url_and_limits_length() -> None:
    candidates = [
        XDraftCandidate(
            score=40,
            title="クラウド運用保守業務委託",
            organization="機関A",
            url="https://example.com/a",
            published_at="2026-02-16T00:00:00+00:00",
            fetched_at="2026-02-16T00:00:00+00:00",
        ),
        XDraftCandidate(
            score=32,
            title="地理情報システム運用支援",
            organization="機関B",
            url="https://example.com/b",
            published_at="2026-02-16T00:00:00+00:00",
            fetched_at="2026-02-16T00:00:00+00:00",
        ),
    ]

    content, item_count = build_x_post_content(
        post_date_jst="2026-02-16",
        candidates=candidates,
        top_n=5,
        lp_url="https://example.com/lp",
    )
    assert "https://example.com/lp" in content
    assert "2026-02-16" in content
    assert item_count >= 1
    assert len(content) <= 280


def test_generate_x_draft_skips_same_day_without_force(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    output_dir = tmp_path / "out"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        item = _item("https://example.com/a", title="調達案件A", organization="機関A")
        item_id = store.upsert_item(item)
        store.record_deliveries(
            run_id="run-1",
            keyword_set_id="set-a",
            records=[
                StoredScoredItem(
                    item_id=item_id,
                    scored_item=ScoredItem(
                        keyword_set_id="set-a",
                        keyword_set_name="A",
                        item=item,
                        score=30,
                        required_matches=("運用", "保守"),
                        boost_matches=(),
                    ),
                )
            ],
            delivered_at=datetime(2026, 2, 16, 0, 30, tzinfo=timezone.utc),
        )

        first = generate_x_draft(
            store=store,
            output_dir=Path(output_dir),
            lp_url="https://example.com/lp",
            top_n=5,
            now_utc=datetime(2026, 2, 16, 1, 0, tzinfo=timezone.utc),
            force=False,
        )
        second = generate_x_draft(
            store=store,
            output_dir=Path(output_dir),
            lp_url="https://example.com/lp",
            top_n=5,
            now_utc=datetime(2026, 2, 16, 2, 0, tzinfo=timezone.utc),
            force=False,
        )

        assert first.skipped is False
        assert first.output_path.exists()
        assert second.skipped is True
    finally:
        store.close()
