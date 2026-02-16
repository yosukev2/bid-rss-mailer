from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
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

CREATE TABLE IF NOT EXISTS x_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_date_jst TEXT NOT NULL UNIQUE,
    generated_at TEXT NOT NULL,
    top_n INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    lp_url TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS x_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_date_jst TEXT NOT NULL UNIQUE,
    posted_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    response_id TEXT NULL,
    response_body TEXT NULL
);

CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    email_norm TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    plan TEXT NOT NULL,
    keyword_sets TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stripe_customers (
    customer_id TEXT PRIMARY KEY,
    email_norm TEXT NOT NULL,
    updated_at TEXT NOT NULL
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

    def purge_older_than(self, *, days: int, now: datetime | None = None) -> None:
        if days <= 0:
            raise ValueError("days must be > 0")
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(days=days)).isoformat()
        with self.connection:
            self.connection.execute(
                "DELETE FROM deliveries WHERE delivered_at < ?",
                (cutoff,),
            )
            self.connection.execute(
                """
                DELETE FROM items
                WHERE fetched_at < ?
                AND id NOT IN (SELECT item_id FROM deliveries)
                """,
                (cutoff,),
            )

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

    def has_x_draft_for_date(self, post_date_jst: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM x_drafts WHERE post_date_jst = ?",
            (post_date_jst,),
        ).fetchone()
        return row is not None

    def record_x_draft(
        self,
        *,
        post_date_jst: str,
        generated_at: str,
        top_n: int,
        item_count: int,
        lp_url: str,
        content: str,
        overwrite: bool = False,
    ) -> None:
        if overwrite:
            sql = """
                INSERT INTO x_drafts (
                    post_date_jst, generated_at, top_n, item_count, lp_url, content
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_date_jst) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    top_n = excluded.top_n,
                    item_count = excluded.item_count,
                    lp_url = excluded.lp_url,
                    content = excluded.content
            """
        else:
            sql = """
                INSERT INTO x_drafts (
                    post_date_jst, generated_at, top_n, item_count, lp_url, content
                ) VALUES (?, ?, ?, ?, ?, ?)
            """
        with self.connection:
            self.connection.execute(
                sql,
                (post_date_jst, generated_at, top_n, item_count, lp_url, content),
            )

    def top_delivered_items(
        self,
        *,
        delivered_at_from: str,
        delivered_at_to: str,
        limit: int,
    ) -> list[sqlite3.Row]:
        if limit <= 0:
            raise ValueError("limit must be > 0")
        rows = self.connection.execute(
            """
            SELECT
                i.id AS item_id,
                i.title AS title,
                i.organization AS organization,
                i.url AS url,
                i.published_at AS published_at,
                i.fetched_at AS fetched_at,
                MAX(d.score) AS score
            FROM deliveries d
            INNER JOIN items i ON i.id = d.item_id
            WHERE d.delivered_at >= ?
              AND d.delivered_at < ?
            GROUP BY i.id, i.title, i.organization, i.url, i.published_at, i.fetched_at
            ORDER BY score DESC, COALESCE(i.published_at, i.fetched_at) DESC, i.id DESC
            LIMIT ?
            """,
            (delivered_at_from, delivered_at_to, limit),
        ).fetchall()
        return rows

    def has_x_post_for_date(self, post_date_jst: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM x_posts WHERE post_date_jst = ?",
            (post_date_jst,),
        ).fetchone()
        return row is not None

    def record_x_post(
        self,
        *,
        post_date_jst: str,
        posted_at: str,
        mode: str,
        status: str,
        response_id: str | None,
        response_body: str | None,
        overwrite: bool = False,
    ) -> None:
        if overwrite:
            sql = """
                INSERT INTO x_posts (
                    post_date_jst, posted_at, mode, status, response_id, response_body
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_date_jst) DO UPDATE SET
                    posted_at = excluded.posted_at,
                    mode = excluded.mode,
                    status = excluded.status,
                    response_id = excluded.response_id,
                    response_body = excluded.response_body
            """
        else:
            sql = """
                INSERT INTO x_posts (
                    post_date_jst, posted_at, mode, status, response_id, response_body
                ) VALUES (?, ?, ?, ?, ?, ?)
            """
        with self.connection:
            self.connection.execute(
                sql,
                (post_date_jst, posted_at, mode, status, response_id, response_body),
            )

    def upsert_subscriber(
        self,
        *,
        email: str,
        email_norm: str,
        status: str,
        plan: str,
        keyword_sets: str,
        now_iso: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO subscribers (
                    email, email_norm, status, plan, keyword_sets, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email_norm) DO UPDATE SET
                    email = excluded.email,
                    status = excluded.status,
                    plan = excluded.plan,
                    keyword_sets = excluded.keyword_sets,
                    updated_at = excluded.updated_at
                """,
                (email, email_norm, status, plan, keyword_sets, now_iso, now_iso),
            )

    def update_subscriber_status(
        self,
        *,
        email_norm: str,
        status: str,
        now_iso: str,
    ) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE subscribers
                SET status = ?, updated_at = ?
                WHERE email_norm = ?
                """,
                (status, now_iso, email_norm),
            )
        return cursor.rowcount > 0

    def list_subscribers(self, status: str | None = None) -> list[sqlite3.Row]:
        if status is None:
            rows = self.connection.execute(
                """
                SELECT id, email, email_norm, status, plan, keyword_sets, created_at, updated_at
                FROM subscribers
                ORDER BY email_norm ASC
                """
            ).fetchall()
            return rows
        rows = self.connection.execute(
            """
            SELECT id, email, email_norm, status, plan, keyword_sets, created_at, updated_at
            FROM subscribers
            WHERE status = ?
            ORDER BY email_norm ASC
            """,
            (status,),
        ).fetchall()
        return rows

    def active_subscriber_emails(self) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT email
            FROM subscribers
            WHERE status = 'active'
            ORDER BY email_norm ASC
            """
        ).fetchall()
        return [str(row["email"]) for row in rows]

    def upsert_stripe_customer(
        self,
        *,
        customer_id: str,
        email_norm: str,
        now_iso: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO stripe_customers (
                    customer_id, email_norm, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(customer_id) DO UPDATE SET
                    email_norm = excluded.email_norm,
                    updated_at = excluded.updated_at
                """,
                (customer_id, email_norm, now_iso),
            )

    def email_norm_by_stripe_customer(self, customer_id: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT email_norm
            FROM stripe_customers
            WHERE customer_id = ?
            """,
            (customer_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["email_norm"])
