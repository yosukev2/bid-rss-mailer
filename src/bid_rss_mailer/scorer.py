from __future__ import annotations

from bid_rss_mailer.config import KeywordSetConfig
from bid_rss_mailer.domain import FeedItem, ScoredItem
from bid_rss_mailer.normalize import contains_term, normalize_text


def _sort_key(scored: ScoredItem) -> tuple[float, float, float, str, str]:
    published_ts = scored.item.published_at.timestamp() if scored.item.published_at else 0.0
    fetched_ts = scored.item.fetched_at.timestamp()
    return (
        -float(scored.score),
        -published_ts,
        -fetched_ts,
        scored.item.organization,
        scored.item.title,
    )


def score_items(
    items: list[FeedItem],
    keyword_sets: list[KeywordSetConfig],
) -> dict[str, list[ScoredItem]]:
    results: dict[str, list[ScoredItem]] = {keyword_set.id: [] for keyword_set in keyword_sets}

    for item in items:
        normalized_title = normalize_text(item.title)
        for keyword_set in keyword_sets:
            if not keyword_set.enabled:
                continue

            required_matches = tuple(
                term for term in keyword_set.required if contains_term(normalized_title, term)
            )
            if len(required_matches) < keyword_set.min_required_matches:
                continue

            excluded_matches = tuple(
                term for term in keyword_set.exclude if contains_term(normalized_title, term)
            )
            if excluded_matches:
                has_exception = any(
                    contains_term(normalized_title, exception) for exception in keyword_set.exclude_exceptions
                )
                if not has_exception:
                    continue

            boost_matches = tuple(term for term in keyword_set.boost if contains_term(normalized_title, term))
            score = len(required_matches) * 10 + len(boost_matches) * 3
            results[keyword_set.id].append(
                ScoredItem(
                    keyword_set_id=keyword_set.id,
                    keyword_set_name=keyword_set.name,
                    item=item,
                    score=score,
                    required_matches=required_matches,
                    boost_matches=boost_matches,
                )
            )

    for keyword_set_id in results:
        results[keyword_set_id].sort(key=_sort_key)
    return results

