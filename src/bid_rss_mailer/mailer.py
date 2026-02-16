from __future__ import annotations

import smtplib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from bid_rss_mailer.config import KeywordSetConfig
from bid_rss_mailer.domain import SourceFailure, StoredScoredItem

JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_address: str
    starttls: bool = True
    use_ssl: bool = False


def _format_item_line(record: StoredScoredItem) -> str:
    item = record.scored_item.item
    date_part = "-"
    if item.published_at is not None:
        date_part = item.published_at.astimezone(JST).strftime("%Y-%m-%d")
    deadline_part = f", deadline={item.deadline_at}" if item.deadline_at else ""
    return (
        f"- {record.scored_item.score} | {item.title} | {item.organization} | "
        f"{date_part}{deadline_part} | {item.url}"
    )


def build_digest_subject(now_jst: datetime) -> str:
    return f"[bid-rss-mailer] {now_jst:%Y-%m-%d} JST 入札/公募サマリ"


def build_digest_body(
    now_jst: datetime,
    keyword_sets: list[KeywordSetConfig],
    selected_by_set: dict[str, list[StoredScoredItem]],
    failures: list[SourceFailure],
    unsubscribe_contact: str,
) -> str:
    lines: list[str] = []
    lines.append(f"実行時刻(JST): {now_jst:%Y-%m-%d %H:%M:%S}")
    lines.append("")
    for keyword_set in keyword_sets:
        lines.append(f"[{keyword_set.name}]")
        records = selected_by_set.get(keyword_set.id, [])
        if not records:
            lines.append("- 0件")
        else:
            for record in records:
                lines.append(_format_item_line(record))
        lines.append("")

    if failures:
        lines.append("取得失敗ソース:")
        for failure in failures:
            lines.append(f"- {failure.source_id} ({failure.source_url}): {failure.error}")
        lines.append("")

    lines.append("免責:")
    lines.append("- 本メールは公式情報へのリンク参照を補助するものです。")
    lines.append("- 応募可否・要件・締切は必ず公式ページで最終確認してください。")
    lines.append(f"- 配信停止: {unsubscribe_contact} へ連絡してください。")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_failure_subject(now_jst: datetime) -> str:
    return f"[bid-rss-mailer][ERROR] {now_jst:%Y-%m-%d %H:%M} JST"


def build_failure_body(now_jst: datetime, context_message: str) -> str:
    return (
        f"実行時刻(JST): {now_jst:%Y-%m-%d %H:%M:%S}\n"
        f"障害内容:\n{context_message}\n"
    )


def send_text_email(
    smtp_config: SmtpConfig,
    to_address: str,
    subject: str,
    body: str,
    max_attempts: int = 3,
    retry_wait_sec: float = 1.0,
) -> None:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    message = EmailMessage()
    message["From"] = smtp_config.from_address
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if smtp_config.use_ssl:
                with smtplib.SMTP_SSL(smtp_config.host, smtp_config.port, timeout=30) as smtp:
                    if smtp_config.user:
                        smtp.login(smtp_config.user, smtp_config.password)
                    smtp.send_message(message)
                return

            with smtplib.SMTP(smtp_config.host, smtp_config.port, timeout=30) as smtp:
                smtp.ehlo()
                if smtp_config.starttls:
                    smtp.starttls()
                    smtp.ehlo()
                if smtp_config.user:
                    smtp.login(smtp_config.user, smtp_config.password)
                smtp.send_message(message)
            return
        except (OSError, smtplib.SMTPException) as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            time.sleep(retry_wait_sec)
    if last_error is not None:
        raise last_error
