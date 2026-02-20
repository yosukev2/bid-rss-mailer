from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from bid_rss_mailer.normalize import normalize_url
from bid_rss_mailer.storage import SQLiteStore

JST = timezone(timedelta(hours=9))
MAX_POST_LENGTH = 280

MODE_AUTO = "auto"
MODE_MANUAL = "manual"
MODE_WEBHOOK = "webhook"
MODE_X_API_V2 = "x_api_v2"
SUPPORTED_MODES = {MODE_AUTO, MODE_MANUAL, MODE_WEBHOOK, MODE_X_API_V2}

ON_MISSING_ROUTE_FAIL = "fail"
ON_MISSING_ROUTE_DRY_RUN_SUCCESS = "dry-run-success"
SUPPORTED_MISSING_ROUTE_POLICIES = {
    ON_MISSING_ROUTE_FAIL,
    ON_MISSING_ROUTE_DRY_RUN_SUCCESS,
}
URL_PATTERN = re.compile(r"https?://[^\s]+")


class LivePublishConfigError(ValueError):
    def __init__(
        self,
        *,
        detail: str,
        missing_requirements: tuple[str, ...],
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.missing_requirements = missing_requirements


@dataclass(frozen=True)
class XPublishResult:
    post_date_jst: str
    mode: str
    route: str
    dry_run: bool
    status: str
    response_id: str | None
    response_body: str | None
    draft_path: Path
    draft_id: int | None
    text_hash: str | None
    planned_text: str
    selection_reason: str
    duplicate_check_result: str
    missing_requirements: tuple[str, ...]
    receipt_path: Path
    skipped: bool


def _post_date_jst(now_utc: datetime) -> str:
    return now_utc.astimezone(JST).date().isoformat()


def _draft_path(draft_dir: Path, post_date_jst: str) -> Path:
    return draft_dir / f"{post_date_jst}.txt"


def _load_draft_text(draft_path: Path) -> tuple[str, str | None]:
    if not draft_path.exists():
        return "", f"x draft file not found: {draft_path}"
    content = draft_path.read_text(encoding="utf-8").strip()
    if not content:
        return "", f"x draft file is empty: {draft_path}"
    return content, None


def _extract_urls(text: str) -> tuple[str, ...]:
    urls: list[str] = []
    for matched in URL_PATTERN.findall(text):
        candidate = matched.rstrip(".,;)")
        if candidate:
            urls.append(candidate)
    return tuple(dict.fromkeys(urls))


def _safe_normalize_url(url: str) -> str:
    try:
        return normalize_url(url)
    except Exception:  # noqa: BLE001
        return url.strip()


def _validate_post_text(
    *,
    text: str,
    lp_url: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    errors: list[str] = []
    payload = text.strip()
    if not payload:
        errors.append("post text is empty")
    if len(payload) > MAX_POST_LENGTH:
        errors.append(f"post text exceeds {MAX_POST_LENGTH} characters: len={len(payload)}")
    urls = _extract_urls(payload)
    if len(urls) > 1:
        errors.append("post text has multiple URLs; keep only LP link")
    if lp_url.strip() and urls:
        expected = _safe_normalize_url(lp_url)
        actual = _safe_normalize_url(urls[0])
        if expected != actual:
            errors.append("post URL does not match LP URL")
    return tuple(errors), urls


def _resolve_route(
    *,
    mode: str,
    webhook_url: str,
    x_user_access_token: str,
    x_api_bearer_token: str,
) -> tuple[str, str, tuple[str, ...], str]:
    webhook = webhook_url.strip()
    user_token = x_user_access_token.strip()
    app_token = x_api_bearer_token.strip()

    if mode == MODE_MANUAL:
        return MODE_MANUAL, "mode=manual: external post disabled", tuple(), ""

    if mode == MODE_WEBHOOK:
        if webhook:
            return MODE_WEBHOOK, "mode=webhook: using X_WEBHOOK_URL", tuple(), ""
        return "", "mode=webhook: X_WEBHOOK_URL is missing", ("X_WEBHOOK_URL",), ""

    if mode == MODE_X_API_V2:
        if user_token:
            return MODE_X_API_V2, "mode=x_api_v2: using X_USER_ACCESS_TOKEN", tuple(), user_token
        if app_token:
            return MODE_X_API_V2, "mode=x_api_v2: using X_API_BEARER_TOKEN", tuple(), app_token
        return "", (
            "mode=x_api_v2: token is missing "
            "(X_USER_ACCESS_TOKEN or X_API_BEARER_TOKEN)"
        ), ("X_USER_ACCESS_TOKEN", "X_API_BEARER_TOKEN"), ""

    if webhook:
        return MODE_WEBHOOK, "mode=auto: selected webhook route", tuple(), ""
    if user_token:
        return MODE_X_API_V2, "mode=auto: selected direct API route (X_USER_ACCESS_TOKEN)", tuple(), user_token
    if app_token:
        return MODE_X_API_V2, "mode=auto: selected direct API route (X_API_BEARER_TOKEN)", tuple(), app_token
    return "", (
        "mode=auto: no publish route is configured "
        "(X_WEBHOOK_URL or token env)"
    ), ("X_WEBHOOK_URL", "X_USER_ACCESS_TOKEN", "X_API_BEARER_TOKEN"), ""


def _duplicate_check_result(existing: object) -> str:
    if existing is None:
        return "no_existing_post_for_jst_date"
    if hasattr(existing, "__getitem__"):
        status = existing["status"]
        mode = existing["mode"]
        text_hash = existing["text_hash"] if "text_hash" in existing.keys() else ""
        return f"existing_post_found status={status} mode={mode} text_hash={text_hash}"
    return "existing_post_found"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_receipt(receipt_path: Path, payload: dict[str, object]) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _publish_webhook(webhook_url: str, text: str, post_date_jst: str) -> tuple[str, str | None, str]:
    target = webhook_url.strip()
    if not target:
        raise ValueError("X_WEBHOOK_URL is required for webhook mode")
    response = requests.post(
        target,
        json={"text": text, "post_date_jst": post_date_jst},
        timeout=20,
    )
    response.raise_for_status()
    response_body = response.text.strip()
    response_id: str | None = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            if "id" in payload:
                response_id = str(payload["id"])
            elif "tweet_id" in payload:
                response_id = str(payload["tweet_id"])
    except ValueError:
        pass
    return "posted", response_id, response_body


def _publish_x_api_v2(token: str, text: str) -> tuple[str, str | None, str]:
    auth_token = token.strip()
    if not auth_token:
        raise ValueError("X token is required for x_api_v2 mode")
    response = requests.post(
        "https://api.twitter.com/2/tweets",
        headers={
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        },
        json={"text": text},
        timeout=20,
    )
    response.raise_for_status()
    response_body = response.text.strip()
    response_id: str | None = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and "id" in data:
                response_id = str(data["id"])
    except ValueError:
        pass
    return "posted", response_id, response_body


def publish_x_post(
    *,
    store: SQLiteStore,
    draft_dir: Path,
    receipt_dir: Path,
    mode: str = MODE_AUTO,
    now_utc: datetime | None = None,
    force: bool = False,
    webhook_url: str = "",
    x_user_access_token: str = "",
    x_api_bearer_token: str = "",
    bearer_token: str = "",
    lp_url: str = "",
    dry_run: bool = False,
    live: bool = False,
    on_missing_route: str = ON_MISSING_ROUTE_FAIL,
) -> XPublishResult:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    if on_missing_route not in SUPPORTED_MISSING_ROUTE_POLICIES:
        raise ValueError(f"unsupported on_missing_route policy: {on_missing_route}")
    if dry_run and live:
        raise ValueError("dry_run and live cannot both be true")

    now = now_utc or datetime.now(timezone.utc)
    post_date_jst = _post_date_jst(now)
    posted_at = now.isoformat()
    draft_path = _draft_path(draft_dir, post_date_jst)
    draft_row = store.x_draft_for_date(post_date_jst)
    draft_id = int(draft_row["id"]) if draft_row is not None else None
    text, load_error = _load_draft_text(draft_path)
    planned_text = text
    text_hash = _text_hash(text) if text else None

    existing = store.x_post_for_date(post_date_jst)
    duplicate_found = existing is not None and not force
    duplicate_result = _duplicate_check_result(existing)
    if duplicate_found:
        receipt_path = receipt_dir / f"{post_date_jst}-{mode}-skipped.json"
        payload = {
            "post_date_jst": post_date_jst,
            "mode": mode,
            "route": str(existing["route"]) if existing is not None else "",
            "status": "skipped",
            "reason": "already posted",
            "posted_at": posted_at,
            "duplicate_check_result": duplicate_result,
            "draft_path": str(draft_path),
        }
        _write_receipt(receipt_path, payload)
        return XPublishResult(
            post_date_jst=post_date_jst,
            mode=mode,
            route=str(existing["route"]) if existing is not None else "",
            dry_run=dry_run,
            status="skipped",
            response_id=None,
            response_body="already posted",
            draft_path=draft_path,
            draft_id=draft_id,
            text_hash=text_hash,
            planned_text=planned_text,
            selection_reason="skip: already posted for JST date",
            duplicate_check_result=duplicate_result,
            missing_requirements=tuple(),
            receipt_path=receipt_path,
            skipped=True,
        )

    direct_api_token = x_api_bearer_token or bearer_token
    route, selection_reason, missing_requirements, direct_token = _resolve_route(
        mode=mode,
        webhook_url=webhook_url,
        x_user_access_token=x_user_access_token,
        x_api_bearer_token=direct_api_token,
    )
    safety_errors, urls = _validate_post_text(text=text, lp_url=lp_url)
    if load_error:
        safety_errors = (*safety_errors, load_error)

    if dry_run:
        dry_run_status = "dry_run"
        if safety_errors:
            dry_run_status = "dry_run_invalid"
        if not route and mode != MODE_MANUAL:
            dry_run_status = "dry_run_no_route"

        receipt_path = receipt_dir / f"{post_date_jst}-{mode}-dry-run.json"
        _write_receipt(
            receipt_path,
            {
                "post_date_jst": post_date_jst,
                "mode": mode,
                "route": route or "none",
                "status": dry_run_status,
                "posted_at": posted_at,
                "draft_path": str(draft_path),
                "draft_id": draft_id,
                "text_hash": text_hash,
                "duplicate_check_result": duplicate_result,
                "selection_reason": selection_reason,
                "planned_text": planned_text,
                "missing_requirements": list(missing_requirements),
                "safety_errors": list(safety_errors),
                "urls": list(urls),
            },
        )
        return XPublishResult(
            post_date_jst=post_date_jst,
            mode=mode,
            route=route or "none",
            dry_run=True,
            status=dry_run_status,
            response_id=None,
            response_body=None,
            draft_path=draft_path,
            draft_id=draft_id,
            text_hash=text_hash,
            planned_text=planned_text,
            selection_reason=selection_reason,
            duplicate_check_result=duplicate_result,
            missing_requirements=missing_requirements,
            receipt_path=receipt_path,
            skipped=False,
        )

    if not route and mode != MODE_MANUAL:
        if on_missing_route == ON_MISSING_ROUTE_DRY_RUN_SUCCESS:
            receipt_path = receipt_dir / f"{post_date_jst}-{mode}-fallback-dry-run.json"
            _write_receipt(
                receipt_path,
                {
                    "post_date_jst": post_date_jst,
                    "mode": mode,
                    "route": "none",
                    "status": "dry_run_no_route",
                    "posted_at": posted_at,
                    "draft_path": str(draft_path),
                    "draft_id": draft_id,
                    "text_hash": text_hash,
                    "duplicate_check_result": duplicate_result,
                    "selection_reason": selection_reason,
                    "planned_text": planned_text,
                    "missing_requirements": list(missing_requirements),
                    "safety_errors": list(safety_errors),
                    "urls": list(urls),
                },
            )
            return XPublishResult(
                post_date_jst=post_date_jst,
                mode=mode,
                route="none",
                dry_run=True,
                status="dry_run_no_route",
                response_id=None,
                response_body=None,
                draft_path=draft_path,
                draft_id=draft_id,
                text_hash=text_hash,
                planned_text=planned_text,
                selection_reason=selection_reason,
                duplicate_check_result=duplicate_result,
                missing_requirements=missing_requirements,
                receipt_path=receipt_path,
                skipped=False,
            )
        raise LivePublishConfigError(
            detail=selection_reason,
            missing_requirements=missing_requirements,
        )

    if safety_errors:
        raise ValueError("x publish safety checks failed: " + "; ".join(safety_errors))

    try:
        if mode == MODE_MANUAL or route == MODE_MANUAL:
            status = "manual_ready"
            response_id = None
            response_body = "manual mode: no external post executed"
            route = MODE_MANUAL
        elif route == MODE_WEBHOOK:
            status, response_id, response_body = _publish_webhook(
                webhook_url=webhook_url,
                text=text,
                post_date_jst=post_date_jst,
            )
        else:
            status, response_id, response_body = _publish_x_api_v2(
                token=direct_token,
                text=text,
            )
    except Exception as exc:  # noqa: BLE001
        failure_reason = str(exc)[:400]
        try:
            store.record_x_post(
                post_date_jst=post_date_jst,
                posted_at=posted_at,
                mode=mode,
                route=route or "none",
                status="failed",
                draft_id=draft_id,
                text_hash=text_hash,
                post_id=None,
                failure_reason=failure_reason,
                response_id=None,
                response_body=failure_reason,
                overwrite=force,
            )
        except Exception:  # noqa: BLE001
            pass
        raise

    safe_response_body = (response_body or "")[:2000]
    failure_reason = None
    store.record_x_post(
        post_date_jst=post_date_jst,
        posted_at=posted_at,
        mode=mode,
        route=route,
        status=status,
        draft_id=draft_id,
        text_hash=text_hash,
        post_id=response_id,
        failure_reason=failure_reason,
        response_id=response_id,
        response_body=safe_response_body,
        overwrite=force,
    )

    receipt_path = receipt_dir / f"{post_date_jst}-{mode}-{route}.json"
    _write_receipt(
        receipt_path=receipt_path,
        payload={
            "post_date_jst": post_date_jst,
            "mode": mode,
                "route": route,
                "status": status,
                "post_id": response_id,
                "response_id": response_id,
                "posted_at": posted_at,
                "draft_path": str(draft_path),
            "draft_id": draft_id,
            "text_hash": text_hash,
            "duplicate_check_result": duplicate_result,
            "selection_reason": selection_reason,
            "planned_text": planned_text,
            "missing_requirements": list(missing_requirements),
            "failure_reason": failure_reason,
            "response_body": safe_response_body,
        },
    )

    return XPublishResult(
        post_date_jst=post_date_jst,
        mode=mode,
        route=route,
        dry_run=False,
        status=status,
        response_id=response_id,
        response_body=safe_response_body,
        draft_path=draft_path,
        draft_id=draft_id,
        text_hash=text_hash,
        planned_text=planned_text,
        selection_reason=selection_reason,
        duplicate_check_result=duplicate_result,
        missing_requirements=missing_requirements,
        receipt_path=receipt_path,
        skipped=False,
    )
