from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from bid_rss_mailer.storage import SQLiteStore

JST = timezone(timedelta(hours=9))

MODE_MANUAL = "manual"
MODE_WEBHOOK = "webhook"
MODE_X_API_V2 = "x_api_v2"
SUPPORTED_MODES = {MODE_MANUAL, MODE_WEBHOOK, MODE_X_API_V2}


@dataclass(frozen=True)
class XPublishResult:
    post_date_jst: str
    mode: str
    status: str
    response_id: str | None
    response_body: str | None
    receipt_path: Path
    skipped: bool


def _post_date_jst(now_utc: datetime) -> str:
    return now_utc.astimezone(JST).date().isoformat()


def _draft_path(draft_dir: Path, post_date_jst: str) -> Path:
    return draft_dir / f"{post_date_jst}.txt"


def _load_draft_text(draft_dir: Path, post_date_jst: str) -> str:
    path = _draft_path(draft_dir, post_date_jst)
    if not path.exists():
        raise FileNotFoundError(f"x draft file not found: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"x draft file is empty: {path}")
    return content


def _write_receipt(receipt_path: Path, payload: dict[str, object]) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _publish_webhook(webhook_url: str, text: str, post_date_jst: str) -> tuple[str, str | None, str]:
    if not webhook_url.strip():
        raise ValueError("X_WEBHOOK_URL is required for webhook mode")
    response = requests.post(
        webhook_url,
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


def _publish_x_api_v2(bearer_token: str, text: str) -> tuple[str, str | None, str]:
    if not bearer_token.strip():
        raise ValueError("X_API_BEARER_TOKEN is required for x_api_v2 mode")
    response = requests.post(
        "https://api.twitter.com/2/tweets",
        headers={
            "Authorization": f"Bearer {bearer_token}",
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
    mode: str,
    now_utc: datetime | None = None,
    force: bool = False,
    webhook_url: str = "",
    bearer_token: str = "",
) -> XPublishResult:
    now = now_utc or datetime.now(timezone.utc)
    post_date_jst = _post_date_jst(now)
    posted_at = now.isoformat()

    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported mode: {mode}")

    if store.has_x_post_for_date(post_date_jst) and not force:
        receipt_path = receipt_dir / f"{post_date_jst}-{mode}.json"
        payload = {
            "post_date_jst": post_date_jst,
            "mode": mode,
            "status": "skipped",
            "reason": "already posted",
            "posted_at": posted_at,
        }
        _write_receipt(receipt_path, payload)
        return XPublishResult(
            post_date_jst=post_date_jst,
            mode=mode,
            status="skipped",
            response_id=None,
            response_body="already posted",
            receipt_path=receipt_path,
            skipped=True,
        )

    text = _load_draft_text(draft_dir=draft_dir, post_date_jst=post_date_jst)
    status: str
    response_id: str | None
    response_body: str | None

    if mode == MODE_MANUAL:
        status = "manual_ready"
        response_id = None
        response_body = "manual mode: no external post executed"
    elif mode == MODE_WEBHOOK:
        status, response_id, response_body = _publish_webhook(
            webhook_url=webhook_url,
            text=text,
            post_date_jst=post_date_jst,
        )
    else:
        status, response_id, response_body = _publish_x_api_v2(
            bearer_token=bearer_token,
            text=text,
        )

    safe_response_body = (response_body or "")[:2000]
    store.record_x_post(
        post_date_jst=post_date_jst,
        posted_at=posted_at,
        mode=mode,
        status=status,
        response_id=response_id,
        response_body=safe_response_body,
        overwrite=force,
    )

    receipt_path = receipt_dir / f"{post_date_jst}-{mode}.json"
    _write_receipt(
        receipt_path=receipt_path,
        payload={
            "post_date_jst": post_date_jst,
            "mode": mode,
            "status": status,
            "response_id": response_id,
            "posted_at": posted_at,
            "draft_path": str(_draft_path(draft_dir=draft_dir, post_date_jst=post_date_jst)),
            "response_body": safe_response_body,
        },
    )

    return XPublishResult(
        post_date_jst=post_date_jst,
        mode=mode,
        status=status,
        response_id=response_id,
        response_body=safe_response_body,
        receipt_path=receipt_path,
        skipped=False,
    )
