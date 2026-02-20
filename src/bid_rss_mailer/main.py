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
from bid_rss_mailer.subscribers import (
    ALLOWED_SUBSCRIBER_STATUS,
    build_subscriber_input,
    parse_keyword_sets,
    keyword_sets_from_json,
    keyword_sets_to_json,
    validate_email,
    validate_status,
)
from bid_rss_mailer.stripe_integration import apply_webhook_payload, create_checkout_session
from bid_rss_mailer.x_draft import generate_x_draft
from bid_rss_mailer.x_publish import (
    MODE_AUTO,
    MODE_MANUAL,
    MODE_WEBHOOK,
    MODE_X_API_V2,
    ON_MISSING_ROUTE_DRY_RUN_SUCCESS,
    ON_MISSING_ROUTE_FAIL,
    LivePublishConfigError,
    publish_x_post,
)

LOGGER = logging.getLogger("bid_rss_mailer")


@dataclass(frozen=True)
class RuntimeSettings:
    admin_email: str | None
    db_path: str
    smtp_config: SmtpConfig | None


def _parse_bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_positive_int_env(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be positive integer") from exc
    if value <= 0:
        raise ConfigError(f"{key} must be positive integer")
    return value


def _require_env(key: str) -> str:
    value = (os.getenv(key) or "").strip()
    if not value:
        raise ConfigError(f"{key} is required")
    return value


def _resolve_db_path(db_path_override: str | None) -> str:
    return (db_path_override or os.getenv("DB_PATH") or "data/app.db").strip()


def load_runtime_settings(
    db_path_override: str | None,
    require_smtp: bool,
    require_admin: bool = True,
) -> RuntimeSettings:
    admin_email = (os.getenv("ADMIN_EMAIL") or "").strip()
    if require_admin and not admin_email:
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

    return RuntimeSettings(
        admin_email=admin_email or None,
        db_path=db_path,
        smtp_config=smtp_config,
    )


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
        require_admin=not args.skip_smtp,
    )
    store = SQLiteStore(settings.db_path)
    try:
        store.initialize()
    finally:
        store.close()
    print("self-test: ok")
    return 0


def _notify_failure(settings: RuntimeSettings, message: str) -> None:
    if not settings.admin_email:
        LOGGER.error("Cannot send failure notification: ADMIN_EMAIL is missing")
        return
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
        require_admin=True,
    )
    if not settings.admin_email:
        raise ConfigError("ADMIN_EMAIL is required")
    max_total_items = _parse_positive_int_env("MAIL_MAX_TOTAL_ITEMS", 30)
    send_admin_copy = _parse_bool_env("SEND_ADMIN_COPY", True)
    unsubscribe_contact = (os.getenv("UNSUBSCRIBE_CONTACT") or settings.admin_email).strip()

    result = run_pipeline(
        sources_path=Path(args.sources),
        keyword_sets_path=Path(args.keywords),
        db_path=settings.db_path,
        admin_email=settings.admin_email,
        smtp_config=settings.smtp_config,
        dry_run=args.dry_run,
        max_total_items=max_total_items,
        send_admin_copy=send_admin_copy,
        unsubscribe_contact=unsubscribe_contact,
    )
    selected_total = sum(len(records) for records in result.selected_by_set.values())
    LOGGER.info(
        "run complete: run_id=%s fetched=%s selected=%s failures=%s digest_sent=%s recipients=%s dry_run=%s",
        result.run_id,
        result.fetched_count,
        selected_total,
        len(result.failures),
        result.digest_sent,
        len(result.recipients),
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
        require_admin=False,
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


def run_x_publish_command(args: argparse.Namespace) -> int:
    if args.live and args.dry_run:
        raise ConfigError("x-publish: --live and --dry-run cannot be used together")
    if not args.live and not args.dry_run:
        raise ConfigError("x-publish: choose either --dry-run or --live")

    settings = load_runtime_settings(
        db_path_override=args.db_path,
        require_smtp=False,
        require_admin=False,
    )

    store = SQLiteStore(settings.db_path)
    try:
        store.initialize()
        try:
            result = publish_x_post(
                store=store,
                draft_dir=Path(args.draft_dir),
                receipt_dir=Path(args.receipt_dir),
                mode=args.mode,
                force=args.force,
                webhook_url=(os.getenv("X_WEBHOOK_URL") or "").strip(),
                x_user_access_token=(os.getenv("X_USER_ACCESS_TOKEN") or "").strip(),
                x_api_bearer_token=(os.getenv("X_API_BEARER_TOKEN") or "").strip(),
                lp_url=(os.getenv("LP_PUBLIC_URL") or os.getenv("APP_BASE_URL") or "").strip(),
                dry_run=args.dry_run,
                live=args.live,
                on_missing_route=args.on_missing_route,
            )
        except LivePublishConfigError as exc:
            missing = ", ".join(exc.missing_requirements) if exc.missing_requirements else "(none)"
            print(f"x-publish config error: {exc.detail}")
            print(f"missing_requirements={missing}")
            return 2
    finally:
        store.close()

    LOGGER.info(
        "x-publish complete: post_date_jst=%s mode=%s route=%s dry_run=%s status=%s skipped=%s receipt=%s",
        result.post_date_jst,
        result.mode,
        result.route,
        result.dry_run,
        result.status,
        result.skipped,
        result.receipt_path,
    )
    LOGGER.info(
        "x-publish detail: selection_reason=%s duplicate_check=%s draft_path=%s draft_id=%s text_hash=%s missing=%s",
        result.selection_reason,
        result.duplicate_check_result,
        result.draft_path,
        result.draft_id,
        result.text_hash,
        ",".join(result.missing_requirements),
    )
    print("planned_text_begin")
    print(result.planned_text)
    print("planned_text_end")
    print(f"selection_reason={result.selection_reason}")
    print(f"draft_path={result.draft_path}")
    print(f"duplicate_check={result.duplicate_check_result}")
    print(result.receipt_path)
    return 0


def run_subscriber_add_command(args: argparse.Namespace) -> int:
    subscriber = build_subscriber_input(
        email=args.email,
        status=args.status,
        plan=args.plan,
        keyword_sets=args.keyword_sets,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    store = SQLiteStore(_resolve_db_path(args.db_path))
    try:
        store.initialize()
        store.upsert_subscriber(
            email=subscriber.email,
            email_norm=subscriber.email_norm,
            status=subscriber.status,
            plan=subscriber.plan,
            keyword_sets=keyword_sets_to_json(subscriber.keyword_sets),
            now_iso=now_iso,
        )
    finally:
        store.close()
    print(f"subscriber upserted: {subscriber.email_norm} status={subscriber.status}")
    return 0


def run_subscriber_stop_command(args: argparse.Namespace) -> int:
    email_norm = validate_email(args.email)
    now_iso = datetime.now(timezone.utc).isoformat()
    store = SQLiteStore(_resolve_db_path(args.db_path))
    try:
        store.initialize()
        updated = store.update_subscriber_status(
            email_norm=email_norm,
            status="stopped",
            now_iso=now_iso,
        )
    finally:
        store.close()
    if not updated:
        raise ConfigError(f"subscriber not found: {email_norm}")
    print(f"subscriber stopped: {email_norm}")
    return 0


def run_subscriber_list_command(args: argparse.Namespace) -> int:
    status = None
    if args.status:
        status = validate_status(args.status)
    store = SQLiteStore(_resolve_db_path(args.db_path))
    try:
        store.initialize()
        rows = store.list_subscribers(status=status)
    finally:
        store.close()

    if args.json:
        payload = [
            {
                "email": row["email"],
                "status": row["status"],
                "plan": row["plan"],
                "keyword_sets": list(keyword_sets_from_json(str(row["keyword_sets"]))),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("email\tstatus\tplan\tkeyword_sets\tupdated_at")
    for row in rows:
        keyword_sets = ",".join(keyword_sets_from_json(str(row["keyword_sets"])))
        print(
            f"{row['email']}\t{row['status']}\t{row['plan']}\t{keyword_sets}\t{row['updated_at']}"
        )
    print(f"count={len(rows)}")
    return 0


def run_stripe_checkout_create_command(args: argparse.Namespace) -> int:
    secret_key = _require_env("STRIPE_SECRET_KEY")
    price_id = (args.price_id or os.getenv("STRIPE_PRICE_ID") or "").strip()
    success_url = (args.success_url or os.getenv("STRIPE_SUCCESS_URL") or "").strip()
    cancel_url = (args.cancel_url or os.getenv("STRIPE_CANCEL_URL") or "").strip()
    if not price_id:
        raise ConfigError("STRIPE_PRICE_ID is required")
    if not success_url:
        raise ConfigError("STRIPE_SUCCESS_URL is required")
    if not cancel_url:
        raise ConfigError("STRIPE_CANCEL_URL is required")

    email_norm = validate_email(args.email)
    plan = (args.plan or os.getenv("STRIPE_DEFAULT_PLAN") or "stripe-monthly").strip()
    keyword_sets = parse_keyword_sets(
        args.keyword_sets
        or os.getenv("STRIPE_DEFAULT_KEYWORD_SETS")
        or "all"
    )
    mock_mode = _parse_bool_env("STRIPE_MOCK_MODE", False)

    result = create_checkout_session(
        secret_key=secret_key,
        price_id=price_id,
        customer_email=email_norm,
        success_url=success_url,
        cancel_url=cancel_url,
        plan=plan,
        keyword_sets=keyword_sets,
        mock_mode=mock_mode,
    )
    LOGGER.info(
        "stripe checkout created: session_id=%s email=%s mock_mode=%s",
        result.session_id,
        email_norm,
        result.mock_mode,
    )
    print(result.checkout_url)
    return 0


def run_stripe_webhook_apply_command(args: argparse.Namespace) -> int:
    payload_path = Path(args.payload)
    payload = payload_path.read_bytes()
    signature = (args.signature or "").strip() or None
    verify_signature = not args.skip_signature_check and _parse_bool_env("STRIPE_VERIFY_SIGNATURE", True)
    webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip() or None
    default_plan = (os.getenv("STRIPE_DEFAULT_PLAN") or "stripe-monthly").strip()
    default_keyword_sets = parse_keyword_sets(
        os.getenv("STRIPE_DEFAULT_KEYWORD_SETS") or "all"
    )

    store = SQLiteStore(_resolve_db_path(args.db_path))
    try:
        store.initialize()
        result = apply_webhook_payload(
            store=store,
            payload=payload,
            signature_header=signature,
            webhook_secret=webhook_secret,
            verify_signature=verify_signature,
            default_plan=default_plan,
            default_keyword_sets=default_keyword_sets,
        )
    finally:
        store.close()

    LOGGER.info(
        "stripe webhook applied: event=%s action=%s email=%s status=%s customer=%s verify_signature=%s",
        result.event_type,
        result.action,
        result.email_norm,
        result.status,
        result.customer_id,
        verify_signature,
    )
    print(
        "event="
        f"{result.event_type} action={result.action} email={result.email_norm} status={result.status}"
    )
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

    x_publish_parser = subparsers.add_parser(
        "x-publish",
        help="Publish today's X draft (auto/manual/webhook/x_api_v2) with --dry-run or --live.",
    )
    x_publish_parser.add_argument("--db-path", default=None)
    x_publish_parser.add_argument("--draft-dir", default=os.getenv("X_DRAFT_OUTPUT_DIR", "out/x-drafts"))
    x_publish_parser.add_argument("--receipt-dir", default="out/x-publish")
    on_missing_route_default = (
        os.getenv("X_PUBLISH_ON_MISSING_ROUTE")
        or ON_MISSING_ROUTE_FAIL
    ).strip()
    if on_missing_route_default not in (ON_MISSING_ROUTE_FAIL, ON_MISSING_ROUTE_DRY_RUN_SUCCESS):
        on_missing_route_default = ON_MISSING_ROUTE_FAIL
    x_publish_parser.add_argument(
        "--mode",
        choices=(MODE_AUTO, MODE_MANUAL, MODE_WEBHOOK, MODE_X_API_V2),
        default=MODE_AUTO,
    )
    x_publish_parser.add_argument("--dry-run", action="store_true")
    x_publish_parser.add_argument("--live", action="store_true")
    x_publish_parser.add_argument(
        "--on-missing-route",
        choices=(ON_MISSING_ROUTE_FAIL, ON_MISSING_ROUTE_DRY_RUN_SUCCESS),
        default=on_missing_route_default,
    )
    x_publish_parser.add_argument("--force", action="store_true")
    x_publish_parser.set_defaults(handler=run_x_publish_command)

    subscriber_add_parser = subparsers.add_parser(
        "subscriber-add",
        help="Add or update subscriber (idempotent by email).",
    )
    subscriber_add_parser.add_argument("--db-path", default=None)
    subscriber_add_parser.add_argument("--email", required=True)
    subscriber_add_parser.add_argument("--status", default="active", choices=tuple(ALLOWED_SUBSCRIBER_STATUS))
    subscriber_add_parser.add_argument("--plan", default="manual")
    subscriber_add_parser.add_argument("--keyword-sets", default="all")
    subscriber_add_parser.set_defaults(handler=run_subscriber_add_command)

    subscriber_stop_parser = subparsers.add_parser(
        "subscriber-stop",
        help="Stop subscriber by email.",
    )
    subscriber_stop_parser.add_argument("--db-path", default=None)
    subscriber_stop_parser.add_argument("--email", required=True)
    subscriber_stop_parser.set_defaults(handler=run_subscriber_stop_command)

    subscriber_list_parser = subparsers.add_parser(
        "subscriber-list",
        help="List subscribers.",
    )
    subscriber_list_parser.add_argument("--db-path", default=None)
    subscriber_list_parser.add_argument("--status", default=None, choices=tuple(ALLOWED_SUBSCRIBER_STATUS))
    subscriber_list_parser.add_argument("--json", action="store_true")
    subscriber_list_parser.set_defaults(handler=run_subscriber_list_command)

    stripe_checkout_parser = subparsers.add_parser(
        "stripe-checkout-create",
        help="Create Stripe checkout session URL for subscription purchase.",
    )
    stripe_checkout_parser.add_argument("--db-path", default=None)
    stripe_checkout_parser.add_argument("--email", required=True)
    stripe_checkout_parser.add_argument("--price-id", default=None)
    stripe_checkout_parser.add_argument("--success-url", default=None)
    stripe_checkout_parser.add_argument("--cancel-url", default=None)
    stripe_checkout_parser.add_argument("--plan", default=None)
    stripe_checkout_parser.add_argument("--keyword-sets", default=None)
    stripe_checkout_parser.set_defaults(handler=run_stripe_checkout_create_command)

    stripe_webhook_parser = subparsers.add_parser(
        "stripe-webhook-apply",
        help="Verify and apply Stripe webhook payload to subscribers DB.",
    )
    stripe_webhook_parser.add_argument("--db-path", default=None)
    stripe_webhook_parser.add_argument("--payload", required=True)
    stripe_webhook_parser.add_argument("--signature", default=None)
    stripe_webhook_parser.add_argument("--skip-signature-check", action="store_true")
    stripe_webhook_parser.set_defaults(handler=run_stripe_webhook_apply_command)

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
                require_admin=False,
            )
            _notify_failure(settings, traceback.format_exc())
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to send failure notification")
        return 1


if __name__ == "__main__":
    sys.exit(main())
