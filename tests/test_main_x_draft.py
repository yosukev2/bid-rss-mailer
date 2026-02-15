from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from bid_rss_mailer import main as main_module


def test_x_draft_failure_triggers_failure_notification(monkeypatch, tmp_path) -> None:
    sent = {"count": 0, "subject": ""}

    def _fake_send_text_email(**kwargs):  # type: ignore[no-untyped-def]
        sent["count"] += 1
        sent["subject"] = kwargs["subject"]

    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.com")
    monkeypatch.setenv("SMTP_STARTTLS", "false")
    monkeypatch.delenv("LP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.setattr(main_module, "send_text_email", _fake_send_text_email)

    db_path = Path(tmp_path) / "app.db"
    exit_code = main_module.main(["x-draft", "--db-path", str(db_path)])

    assert exit_code == 1
    assert sent["count"] == 1
    assert "[ERROR]" in sent["subject"]


def test_x_publish_failure_triggers_failure_notification(monkeypatch, tmp_path) -> None:
    sent = {"count": 0, "subject": ""}

    def _fake_send_text_email(**kwargs):  # type: ignore[no-untyped-def]
        sent["count"] += 1
        sent["subject"] = kwargs["subject"]

    now_jst = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))
    post_date_jst = now_jst.date().isoformat()
    draft_dir = Path(tmp_path) / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / f"{post_date_jst}.txt").write_text("draft", encoding="utf-8")

    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.com")
    monkeypatch.setenv("SMTP_STARTTLS", "false")
    monkeypatch.delenv("X_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(main_module, "send_text_email", _fake_send_text_email)

    db_path = Path(tmp_path) / "app.db"
    receipt_dir = Path(tmp_path) / "receipts"
    exit_code = main_module.main(
        [
            "x-publish",
            "--db-path",
            str(db_path),
            "--draft-dir",
            str(draft_dir),
            "--receipt-dir",
            str(receipt_dir),
            "--mode",
            "webhook",
            "--force",
        ]
    )

    assert exit_code == 1
    assert sent["count"] == 1
    assert "[ERROR]" in sent["subject"]
