from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bid_rss_mailer.storage import SQLiteStore
from bid_rss_mailer.x_publish import (
    MODE_AUTO,
    MODE_MANUAL,
    MODE_WEBHOOK,
    MODE_X_API_V2,
    ON_MISSING_ROUTE_DRY_RUN_SUCCESS,
    LivePublishConfigError,
    publish_x_post,
)


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


def test_publish_dry_run_never_posts_and_reports_plan(monkeypatch, tmp_path) -> None:
    def _unexpected_post(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("requests.post must not be called in dry-run")

    monkeypatch.setattr("bid_rss_mailer.x_publish.requests.post", _unexpected_post)

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
            mode=MODE_AUTO,
            now_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
            dry_run=True,
        )
        assert result.dry_run is True
        assert result.status == "dry_run_no_route"
        assert "本日の注目公告" in result.planned_text
        assert "no_existing_post_for_jst_date" in result.duplicate_check_result
        assert "X_WEBHOOK_URL" in result.missing_requirements
    finally:
        store.close()


def test_publish_live_missing_route_raises_config_error(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    draft_dir = tmp_path / "drafts"
    receipt_dir = tmp_path / "receipts"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        _prepare_draft(draft_dir, "2026-02-16")
        with pytest.raises(LivePublishConfigError) as exc_info:
            publish_x_post(
                store=store,
                draft_dir=draft_dir,
                receipt_dir=receipt_dir,
                mode=MODE_AUTO,
                now_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
                live=True,
            )
        assert "no publish route" in str(exc_info.value)
        assert "X_WEBHOOK_URL" in exc_info.value.missing_requirements
    finally:
        store.close()


def test_publish_live_missing_route_can_fallback_to_dry_run(tmp_path) -> None:
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
            mode=MODE_AUTO,
            now_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
            live=True,
            on_missing_route=ON_MISSING_ROUTE_DRY_RUN_SUCCESS,
        )
        assert result.dry_run is True
        assert result.status == "dry_run_no_route"
        assert result.route == "none"
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


def test_publish_webhook_failure_records_failed_status(monkeypatch, tmp_path) -> None:
    def _raise_post(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("webhook timeout")

    monkeypatch.setattr("bid_rss_mailer.x_publish.requests.post", _raise_post)

    db_path = tmp_path / "app.db"
    draft_dir = tmp_path / "drafts"
    receipt_dir = tmp_path / "receipts"
    store = SQLiteStore(str(db_path))
    store.initialize()
    try:
        _prepare_draft(draft_dir, "2026-02-16")
        with pytest.raises(RuntimeError):
            publish_x_post(
                store=store,
                draft_dir=draft_dir,
                receipt_dir=receipt_dir,
                mode=MODE_WEBHOOK,
                now_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
                force=False,
                webhook_url="https://example.com/hook",
                live=True,
            )
        row = store.x_post_for_date("2026-02-16")
        assert row is not None
        assert row["status"] == "failed"
        assert "timeout" in str(row["failure_reason"])
    finally:
        store.close()
