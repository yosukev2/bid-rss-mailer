from __future__ import annotations

import calendar
import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import requests
from requests.adapters import HTTPAdapter

from bid_rss_mailer.config import SourceConfig
from bid_rss_mailer.domain import FeedItem, SourceFailure
from bid_rss_mailer.normalize import extract_deadline

USER_AGENT = "bid-rss-mailer/0.1 (+https://github.com/yosukev2/bid-rss-mailer)"


class LegacyTLSAdapter(HTTPAdapter):
    """Enable legacy renegotiation where OpenSSL supports it."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self._ssl_context = ssl.create_default_context()
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            self._ssl_context.options |= ssl.OP_LEGACY_SERVER_CONNECT
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        kwargs["ssl_context"] = self._ssl_context
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["ssl_context"] = self._ssl_context
        return super().proxy_manager_for(*args, **kwargs)


def _parse_published(entry: dict[str, Any]) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                continue
    return None


def fetch_source(session: requests.Session, source: SourceConfig) -> tuple[list[FeedItem], SourceFailure | None]:
    attempts = source.retries + 1
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                source.url,
                timeout=source.timeout_sec,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"invalid feed payload: {parsed.bozo_exception}")

            fetched_at = datetime.now(timezone.utc)
            items: list[FeedItem] = []
            for entry in parsed.entries:
                title = (entry.get("title") or "").strip()
                url = (entry.get("link") or entry.get("id") or "").strip()
                if not title or not url:
                    continue
                description = (entry.get("summary") or entry.get("description") or "").strip()
                deadline_at = extract_deadline(f"{title} {description}")
                items.append(
                    FeedItem(
                        source_id=source.id,
                        organization=source.organization,
                        title=title,
                        url=url,
                        published_at=_parse_published(entry),
                        fetched_at=fetched_at,
                        description=description,
                        deadline_at=deadline_at,
                    )
                )
            return items, None
        except Exception as exc:  # noqa: BLE001
            last_error = f"attempt {attempt}/{attempts}: {exc}"
    return [], SourceFailure(source_id=source.id, source_url=source.url, error=last_error or "unknown error")


def fetch_all_sources(sources: list[SourceConfig]) -> tuple[list[FeedItem], list[SourceFailure]]:
    enabled_sources = [source for source in sources if source.enabled]
    if not enabled_sources:
        return [], []

    items: list[FeedItem] = []
    failures: list[SourceFailure] = []
    with requests.Session() as session:
        session.mount("https://", LegacyTLSAdapter())
        for source in enabled_sources:
            source_items, failure = fetch_source(session=session, source=source)
            items.extend(source_items)
            if failure:
                failures.append(failure)
    return items, failures
