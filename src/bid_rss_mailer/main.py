from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bid_rss_mailer.config import ConfigError, load_keyword_sets_config, load_sources_config
from bid_rss_mailer.mailer import JST, SmtpConfig, build_failure_body, build_failure_subject, send_text_email
from bid_rss_mailer.pipeline import run_pipeline
from bid_rss_mailer.storage import SQLiteStore
from bid_rss_mailer.x_draft import generate_x_draft

LOGGER = logging.getLogger("bid_rss_mailer")


@dataclass(frozen=True)
class RuntimeSettings:
    admin_email: str
    db_path: str
    smtp_config: SmtpConfig | None


def _parse_bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_runtime_settings(db_path_override: str | None, require_smtp: bool) -> RuntimeSettings:
    admin_email = (os.getenv("ADMIN_EMAIL") or "").strip()
    if not admin_email:
        raise ConfigError("ADMIN_EMAIL is required")

    db_path = (db_path_override or os.getenv("DB_PATH") or "data/app.db").strip()
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_port_raw = (os.getenv("SMTP_PORT") or "").strip()
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_pass = (os.getenv("SMTP_PASS") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or "").strip()

    smtp_config: SmtpConfig | None = None
    has_any_smtp = any([smtp_host, smtp_port_raw, smtp_user, smtp_pass, smtp_from])
    if has_any_smtp or require_smtp:
        missing = [key for key, value in {
            "SMTP_HOST": smtp_host,
            "SMTP_PORT": smtp_port_raw,
            "SMTP_FROM": smtp_from,
        }.items() if not value]
        if missing:
            raise ConfigError(f"Missing SMTP env: {', '.join(missing)}")
        try:
            smtp_port = int(smtp_port_raw)
        except ValueError as exc:
            raise ConfigError("SMTP_PORT must be integer") from exc
        smtp_config = SmtpConfig(
            host=smtp_host,
            port=smtp_port,
            user=smtp_user,
            password=smtp_pass,
            from_address=smtp_from,
            starttls=_parse_bool_env("SMTP_STARTTLS", True),
            use_ssl=_parse_bool_env("SMTP_USE_SSL", smtp_port == 465),
        )

    if require_smtp and smtp_config is None:
        raise ConfigError("SMTP env is required")

    return RuntimeSettings(admin_email=admin_email, db_path=db_path, smtp_config=smtp_config)


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_self_test(args: argparse.Namespace) -> int:
    load_sources_config(args.sources)
    load_keyword_sets_config(args.keywords)
    settings = load_runtime_settings(
        db_path_override=args.db_path,
        require_smtp=not args.skip_smtp,
    )
    store = SQLiteStore(settings.db_path)
    try:
        store.initialize()
    finally:
        store.close()
    print("self-test: ok")
    return 0


def _notify_failure(settings: RuntimeSettings, message: str) -> None:
    if settings.smtp_config is None:
        LOGGER.error("Cannot send failure notification: SMTP config is missing")
        return
    now_jst = datetime.now(timezone.utc).astimezone(JST)
    send_text_email(
        smtp_config=settings.smtp_config,
        to_address=settings.admin_email,
        subject=build_failure_subject(now_jst),
        body=build_failure_body(now_jst, message),
    )


def run_job(args: argparse.Namespace) -> int:
    settings = load_runtime_settings(
        db_path_override=args.db_path,
        require_smtp=not args.dry_run,
    )
    result = run_pipeline(
        sources_path=Path(args.sources),
        keyword_sets_path=Path(args.keywords),
        db_path=settings.db_path,
        admin_email=settings.admin_email,
        smtp_config=settings.smtp_config,
        dry_run=args.dry_run,
    )
    selected_total = sum(len(records) for records in result.selected_by_set.values())
    LOGGER.info(
        "run complete: run_id=%s fetched=%s selected=%s failures=%s digest_sent=%s dry_run=%s",
        result.run_id,
        result.fetched_count,
        selected_total,
        len(result.failures),
        result.digest_sent,
        args.dry_run,
    )
    should_warn = bool(result.failures) or result.fetched_count == 0
    if should_warn and not args.dry_run and settings.smtp_config is not None:
        try:
            now_jst = datetime.now(timezone.utc).astimezone(JST)
            headline = "RSS取得失敗ソースが発生しました。"
            if result.fetched_count == 0:
                headline = "取得結果が0件です（全ソース失敗または全件欠損の可能性）。"
            message = "\n".join(
                [
                    headline,
                    "",
                    *[
                        f"- {failure.source_id} ({failure.source_url}): {failure.error}"
                        for failure in result.failures
                    ],
                ]
            )
            send_text_email(
                smtp_config=settings.smtp_config,
                to_address=settings.admin_email,
                subject=f"[bid-rss-mailer][WARN] {now_jst:%Y-%m-%d %H:%M} JST feed fetch failures",
                body=message,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to send fetch failure warning")
    return 0


def run_x_draft_command(args: argparse.Namespace) -> int:
    settings = load_runtime_settings(
        db_path_override=args.db_path,
        require_smtp=False,
    )
    lp_url = (args.lp_url or os.getenv("LP_PUBLIC_URL") or os.getenv("APP_BASE_URL") or "").strip()
    if not lp_url:
        raise ConfigError("LP_PUBLIC_URL (or APP_BASE_URL / --lp-url) is required for x-draft")

    output_dir = Path(args.output_dir)
    store = SQLiteStore(settings.db_path)
    try:
        store.initialize()
        result = generate_x_draft(
            store=store,
            output_dir=output_dir,
            lp_url=lp_url,
            top_n=args.top_n,
            force=args.force,
        )
    finally:
        store.close()

    LOGGER.info(
        "x-draft complete: post_date_jst=%s skipped=%s item_count=%s output=%s",
        result.post_date_jst,
        result.skipped,
        result.item_count,
        result.output_path,
    )
    print(result.output_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect RSS bids and send email digest.")
    parser.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run feed collection and mail delivery.")
    run_parser.add_argument("--sources", default="data/sources.yaml")
    run_parser.add_argument("--keywords", default="data/keyword_sets.yaml")
    run_parser.add_argument("--db-path", default=None)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(handler=run_job)

    self_test_parser = subparsers.add_parser("self-test", help="Validate config/env and DB init.")
    self_test_parser.add_argument("--sources", default="data/sources.yaml")
    self_test_parser.add_argument("--keywords", default="data/keyword_sets.yaml")
    self_test_parser.add_argument("--db-path", default=None)
    self_test_parser.add_argument("--skip-smtp", action="store_true")
    self_test_parser.set_defaults(handler=run_self_test)

    x_draft_parser = subparsers.add_parser(
        "x-draft",
        help="Generate X post draft from today's delivered items.",
    )
    x_draft_parser.add_argument("--db-path", default=None)
    x_draft_parser.add_argument("--output-dir", default="out/x-drafts")
    x_draft_parser.add_argument("--top-n", type=int, default=5)
    x_draft_parser.add_argument("--lp-url", default=None)
    x_draft_parser.add_argument("--force", action="store_true")
    x_draft_parser.set_defaults(handler=run_x_draft_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Unhandled error: %s", exc)
        settings: RuntimeSettings | None = None
        try:
            settings = load_runtime_settings(
                db_path_override=getattr(args, "db_path", None),
                require_smtp=False,
            )
            _notify_failure(settings, traceback.format_exc())
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to send failure notification")
        return 1


if __name__ == "__main__":
    sys.exit(main())
