from datetime import datetime, timezone

from bid_rss_mailer.config import KeywordSetConfig
from bid_rss_mailer.domain import FeedItem
from bid_rss_mailer.scorer import score_items


def _item(title: str) -> FeedItem:
    now = datetime.now(timezone.utc)
    return FeedItem(
        source_id="source-1",
        organization="Org",
        title=title,
        url=f"https://example.com/{title}",
        published_at=now,
        fetched_at=now,
        description="",
        deadline_at=None,
    )


def test_score_items_required_boost_and_exclude() -> None:
    keyword_set = KeywordSetConfig(
        id="set-a",
        name="A",
        enabled=True,
        min_required_matches=2,
        required=("運用", "保守", "システム"),
        boost=("クラウド",),
        exclude=("工事",),
        exclude_exceptions=(),
        top_n=10,
    )
    items = [
        _item("システム運用保守 クラウド業務委託"),
        _item("システム運用保守 工事"),
    ]

    scored = score_items(items=items, keyword_sets=[keyword_set])
    assert len(scored["set-a"]) == 1
    assert scored["set-a"][0].score == 33


def test_score_items_exclude_exception_allows_item() -> None:
    keyword_set = KeywordSetConfig(
        id="set-c",
        name="C",
        enabled=True,
        min_required_matches=2,
        required=("調査", "研究"),
        boost=("PoC",),
        exclude=("印刷",),
        exclude_exceptions=("デジタル印刷",),
        top_n=10,
    )
    items = [_item("研究調査 デジタル印刷 PoC")]

    scored = score_items(items=items, keyword_sets=[keyword_set])
    assert len(scored["set-c"]) == 1

