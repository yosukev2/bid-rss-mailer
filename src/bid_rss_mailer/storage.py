from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from bid_rss_mailer.domain import FeedItem, StoredScoredItem
from bid_rss_mailer.normalize import stable_url_key

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    organization TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    url_key TEXT NOT NULL UNIQUE,
    published_at TEXT NULL,
    deadline_at TEXT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    keyword_set_id TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    delivered_at TEXT NOT NULL,
    UNIQUE(keyword_set_id, item_id),
    FOREIGN KEY(item_id) REFERENCES items(id)
);
"""


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        with self.connection:
            self.connection.executescript(SCHEMA_SQL)

    def upsert_item(self, item: FeedItem) -> int:
        published_text = item.published_at.isoformat() if item.published_at else None
        url_key = stable_url_key(item.url)
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO items (
                    source_id, organization, title, url, url_key, published_at, deadline_at, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.source_id,
                    item.organization,
                    item.title,
                    item.url,
                    url_key,
                    published_text,
                    item.deadline_at,
                    item.fetched_at.isoformat(),
                ),
            )
            if cursor.rowcount == 0:
                self.connection.execute(
                    """
                    UPDATE items
                    SET source_id = ?,
                        organization = ?,
                        title = ?,
                        url = ?,
                        published_at = COALESCE(?, published_at),
                        deadline_at = COALESCE(?, deadline_at),
                        fetched_at = ?
                    WHERE url_key = ?
                    """,
                    (
                        item.source_id,
                        item.organization,
                        item.title,
                        item.url,
                        published_text,
                        item.deadline_at,
                        item.fetched_at.isoformat(),
                        url_key,
                    ),
                )
            row = self.connection.execute("SELECT id FROM items WHERE url_key = ?", (url_key,)).fetchone()
            if row is None:
                raise RuntimeError(f"Failed to load item id for url_key={url_key}")
            return int(row["id"])

    def delivered_item_ids(self, keyword_set_id: str, item_ids: Iterable[int]) -> set[int]:
        item_id_list = list(item_ids)
        if not item_id_list:
            return set()
        placeholders = ",".join("?" for _ in item_id_list)
        rows = self.connection.execute(
            f"""
            SELECT item_id
            FROM deliveries
            WHERE keyword_set_id = ?
            AND item_id IN ({placeholders})
            """,
            (keyword_set_id, *item_id_list),
        ).fetchall()
        return {int(row["item_id"]) for row in rows}

    def record_deliveries(
        self,
        run_id: str,
        keyword_set_id: str,
        records: list[StoredScoredItem],
        delivered_at: datetime | None = None,
    ) -> None:
        if not records:
            return
        timestamp = (delivered_at or datetime.now(timezone.utc)).isoformat()
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO deliveries (
                    run_id, keyword_set_id, item_id, score, delivered_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        keyword_set_id,
                        record.item_id,
                        record.scored_item.score,
                        timestamp,
                    )
                    for record in records
                ],
            )

