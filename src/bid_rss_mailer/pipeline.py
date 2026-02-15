from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bid_rss_mailer.config import KeywordSetConfig, load_keyword_sets_config, load_sources_config
from bid_rss_mailer.domain import FeedItem, ScoredItem, SourceFailure, StoredScoredItem
from bid_rss_mailer.fetcher import fetch_all_sources
from bid_rss_mailer.mailer import (
    JST,
    SmtpConfig,
    build_digest_body,
    build_digest_subject,
    send_text_email,
)
from bid_rss_mailer.scorer import score_items
from bid_rss_mailer.storage import SQLiteStore


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    fetched_count: int
    selected_by_set: dict[str, list[StoredScoredItem]]
    failures: list[SourceFailure]
    digest_sent: bool


def _attach_item_ids(store: SQLiteStore, items: list[FeedItem]) -> dict[str, int]:
    item_ids: dict[str, int] = {}
    for item in items:
        # URL一意キーが同じ場合、同一item_idへ正規化される。
        item_ids[item.url] = store.upsert_item(item)
    return item_ids


def _filter_new_records(
    store: SQLiteStore,
    keyword_set: KeywordSetConfig,
    scored_items_by_set: dict[str, list[ScoredItem]],
    item_ids_by_url: dict[str, int],
) -> list[StoredScoredItem]:
    scored_items = scored_items_by_set.get(keyword_set.id, [])
    stored = [
        StoredScoredItem(item_id=item_ids_by_url[scored.item.url], scored_item=scored)
        for scored in scored_items
        if scored.item.url in item_ids_by_url
    ]
    if not stored:
        return []

    delivered_ids = store.delivered_item_ids(
        keyword_set_id=keyword_set.id,
        item_ids=[record.item_id for record in stored],
    )
    new_records = [record for record in stored if record.item_id not in delivered_ids]

    deduped_records: list[StoredScoredItem] = []
    seen_item_ids: set[int] = set()
    for record in new_records:
        if record.item_id in seen_item_ids:
            continue
        seen_item_ids.add(record.item_id)
        deduped_records.append(record)
    return deduped_records[: keyword_set.top_n]


def run_pipeline(
    sources_path: Path,
    keyword_sets_path: Path,
    db_path: str,
    admin_email: str,
    smtp_config: SmtpConfig | None,
    dry_run: bool = False,
) -> PipelineResult:
    sources = load_sources_config(sources_path)
    keyword_sets = load_keyword_sets_config(keyword_sets_path)

    store = SQLiteStore(db_path)
    try:
        store.initialize()
        items, failures = fetch_all_sources(sources=sources)
        scored_by_set = score_items(items=items, keyword_sets=keyword_sets)
        item_ids_by_url = _attach_item_ids(store=store, items=items)

        selected_by_set: dict[str, list[StoredScoredItem]] = {}
        for keyword_set in keyword_sets:
            if not keyword_set.enabled:
                continue
            selected_by_set[keyword_set.id] = _filter_new_records(
                store=store,
                keyword_set=keyword_set,
                scored_items_by_set=scored_by_set,
                item_ids_by_url=item_ids_by_url,
            )

        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        now_jst = datetime.now(timezone.utc).astimezone(JST)
        digest_subject = build_digest_subject(now_jst=now_jst)
        digest_body = build_digest_body(
            now_jst=now_jst,
            keyword_sets=keyword_sets,
            selected_by_set=selected_by_set,
            failures=failures,
        )

        digest_sent = False
        if dry_run:
            store.purge_older_than(days=30)
            return PipelineResult(
                run_id=run_id,
                fetched_count=len(items),
                selected_by_set=selected_by_set,
                failures=failures,
                digest_sent=False,
            )

        if smtp_config is None:
            raise RuntimeError("SMTP config is required when dry_run is false")

        send_text_email(
            smtp_config=smtp_config,
            to_address=admin_email,
            subject=digest_subject,
            body=digest_body,
        )
        digest_sent = True

        delivered_at = datetime.now(timezone.utc)
        for keyword_set_id, records in selected_by_set.items():
            store.record_deliveries(
                run_id=run_id,
                keyword_set_id=keyword_set_id,
                records=records,
                delivered_at=delivered_at,
            )
        store.purge_older_than(days=30)

        return PipelineResult(
            run_id=run_id,
            fetched_count=len(items),
            selected_by_set=selected_by_set,
            failures=failures,
            digest_sent=digest_sent,
        )
    finally:
        store.close()
