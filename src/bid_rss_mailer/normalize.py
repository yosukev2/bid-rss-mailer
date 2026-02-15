from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}

_SPACE_PATTERN = re.compile(r"\s+")
_DEADLINE_PATTERNS = [
    re.compile(
        r"(?<!\d)(?P<year>\d{4})[./-年]\s*(?P<month>1[0-2]|0?[1-9])[./-月]\s*(?P<day>3[01]|[12]\d|0?[1-9])\s*日?"
    ),
    re.compile(r"(?<!\d)(?P<year>\d{4})年\s*(?P<month>1[0-2]|0?[1-9])月\s*(?P<day>3[01]|[12]\d|0?[1-9])日"),
]


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    return _SPACE_PATTERN.sub(" ", normalized).strip()


def contains_term(normalized_text: str, term: str) -> bool:
    return normalize_text(term) in normalized_text


def normalize_url(url: str) -> str:
    parsed = urlsplit((url or "").strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    filtered_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(filtered_pairs), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def stable_url_key(url: str) -> str:
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_deadline(text: str) -> str | None:
    normalized = normalize_text(text)
    for pattern in _DEADLINE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        try:
            parsed = date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            continue
        return parsed.isoformat()
    return None

