from __future__ import annotations

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
