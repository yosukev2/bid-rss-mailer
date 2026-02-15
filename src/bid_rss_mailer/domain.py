from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FeedItem:
    source_id: str
    organization: str
    title: str
    url: str
    published_at: datetime | None
    fetched_at: datetime
    description: str
    deadline_at: str | None


@dataclass(frozen=True)
class SourceFailure:
    source_id: str
    source_url: str
    error: str


@dataclass(frozen=True)
class ScoredItem:
    keyword_set_id: str
    keyword_set_name: str
    item: FeedItem
    score: int
    required_matches: tuple[str, ...]
    boost_matches: tuple[str, ...]


@dataclass(frozen=True)
class StoredScoredItem:
    item_id: int
    scored_item: ScoredItem

