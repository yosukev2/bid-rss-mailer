from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bid_rss_mailer.storage import SQLiteStore
from bid_rss_mailer.x_publish import MODE_MANUAL, MODE_WEBHOOK, MODE_X_API_V2, publish_x_post


def _prepare_draft(draft_dir: Path, post_date_jst: str) -> None:
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / f"{post_date_jst}.txt").write_text(
        "【本日の注目公告 / 無料版】2026-02-16 JST\n詳細（有料版）: https://example.com/lp\n",
        encoding="utf-8",
    )


def test_publish_manual_skips_when_already_posted(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    draft_dir = tmp_path / "drafts"
    receipt_dir = tmp_path / "receipts"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        post_date_jst = "2026-02-16"
        _prepare_draft(draft_dir, post_date_jst)
        first = publish_x_post(
            store=store,
            draft_dir=draft_dir,
            receipt_dir=receipt_dir,
            mode=MODE_MANUAL,
            now_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
            force=False,
        )
        second = publish_x_post(
            store=store,
            draft_dir=draft_dir,
            receipt_dir=receipt_dir,
            mode=MODE_MANUAL,
            now_utc=datetime(2026, 2, 16, 1, 0, tzinfo=timezone.utc),
            force=False,
        )

        assert first.skipped is False
        assert second.skipped is True
    finally:
        store.close()


def test_publish_webhook_requires_url(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    draft_dir = tmp_path / "drafts"
    receipt_dir = tmp_path / "receipts"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        _prepare_draft(draft_dir, "2026-02-16")
        with pytest.raises(ValueError):
            publish_x_post(
                store=store,
                draft_dir=draft_dir,
                receipt_dir=receipt_dir,
                mode=MODE_WEBHOOK,
                now_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
                force=False,
                webhook_url="",
            )
    finally:
        store.close()


def test_publish_webhook_records_response(monkeypatch, tmp_path) -> None:
    class _Resp:
        status_code = 200
        text = "{\"id\":\"abc-123\"}"

        def raise_for_status(self) -> None:
            return None

        def json(self):  # type: ignore[no-untyped-def]
            return {"id": "abc-123"}

    monkeypatch.setattr("bid_rss_mailer.x_publish.requests.post", lambda *args, **kwargs: _Resp())

    db_path = tmp_path / "app.db"
    draft_dir = tmp_path / "drafts"
    receipt_dir = tmp_path / "receipts"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        _prepare_draft(draft_dir, "2026-02-16")
        result = publish_x_post(
            store=store,
            draft_dir=draft_dir,
            receipt_dir=receipt_dir,
            mode=MODE_WEBHOOK,
            now_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
            force=False,
            webhook_url="https://example.com/hook",
        )
        assert result.status == "posted"
        assert result.response_id == "abc-123"
        receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
        assert receipt["status"] == "posted"
        assert receipt["response_id"] == "abc-123"
    finally:
        store.close()


def test_publish_x_api_v2_records_response(monkeypatch, tmp_path) -> None:
    class _Resp:
        status_code = 201
        text = "{\"data\":{\"id\":\"tweet-1\"}}"

        def raise_for_status(self) -> None:
            return None

        def json(self):  # type: ignore[no-untyped-def]
            return {"data": {"id": "tweet-1"}}

    monkeypatch.setattr("bid_rss_mailer.x_publish.requests.post", lambda *args, **kwargs: _Resp())

    db_path = tmp_path / "app.db"
    draft_dir = tmp_path / "drafts"
    receipt_dir = tmp_path / "receipts"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        _prepare_draft(draft_dir, "2026-02-16")
        result = publish_x_post(
            store=store,
            draft_dir=draft_dir,
            receipt_dir=receipt_dir,
            mode=MODE_X_API_V2,
            now_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
            force=False,
            bearer_token="token",
        )
        assert result.status == "posted"
        assert result.response_id == "tweet-1"
    finally:
        store.close()
