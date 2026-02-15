from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from bid_rss_mailer.storage import SQLiteStore

JST = timezone(timedelta(hours=9))
MAX_POST_LENGTH = 280


@dataclass(frozen=True)
class XDraftCandidate:
    score: int
    title: str
    organization: str
    url: str
    published_at: str | None
    fetched_at: str


@dataclass(frozen=True)
class XDraftResult:
    post_date_jst: str
    output_path: Path
    content: str
    item_count: int
    skipped: bool


def _trim(value: str, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def _make_header(post_date_jst: str) -> list[str]:
    return [
        f"【本日の注目公告 / 無料版】{post_date_jst} JST",
        "上位案件（ルールベース抽出）",
    ]


def build_x_post_content(
    *,
    post_date_jst: str,
    candidates: list[XDraftCandidate],
    top_n: int,
    lp_url: str,
) -> tuple[str, int]:
    if not lp_url.strip():
        raise ValueError("lp_url is required")
    if top_n <= 0:
        raise ValueError("top_n must be > 0")

    header = _make_header(post_date_jst)
    footer = [f"詳細（有料版）: {lp_url.strip()}", "#入札 #公募 #官公庁"]

    lines: list[str] = [*header]
    item_count = 0

    if candidates:
        for index, candidate in enumerate(candidates[:top_n], start=1):
            line = f"{index}. {_trim(candidate.title, 36)}（{_trim(candidate.organization, 14)}）"
            candidate_lines = [*lines, line, *footer]
            if len("\n".join(candidate_lines)) > MAX_POST_LENGTH:
                break
            lines.append(line)
            item_count += 1
    if item_count == 0:
        lines.append("本日は無料版に掲載する新規案件がありません。")

    final_lines = [*lines, *footer]
    content = "\n".join(final_lines)
    if len(content) > MAX_POST_LENGTH:
        raise ValueError(
            f"x draft content exceeds {MAX_POST_LENGTH} characters: len={len(content)} lp_url={lp_url}"
        )
    return content, item_count


def _day_range_utc(now_utc: datetime) -> tuple[str, str, str]:
    now_jst = now_utc.astimezone(JST)
    post_date_jst = now_jst.date().isoformat()
    start_jst = datetime.combine(now_jst.date(), time.min, tzinfo=JST)
    end_jst = start_jst + timedelta(days=1)
    return (
        post_date_jst,
        start_jst.astimezone(timezone.utc).isoformat(),
        end_jst.astimezone(timezone.utc).isoformat(),
    )


def generate_x_draft(
    *,
    store: SQLiteStore,
    output_dir: Path,
    lp_url: str,
    top_n: int = 5,
    now_utc: datetime | None = None,
    force: bool = False,
) -> XDraftResult:
    now = now_utc or datetime.now(timezone.utc)
    generated_at = now.isoformat()
    post_date_jst, start_utc, end_utc = _day_range_utc(now)
    output_path = output_dir / f"{post_date_jst}.txt"

    if store.has_x_draft_for_date(post_date_jst) and not force:
        existing = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        return XDraftResult(
            post_date_jst=post_date_jst,
            output_path=output_path,
            content=existing,
            item_count=0,
            skipped=True,
        )

    rows = store.top_delivered_items(
        delivered_at_from=start_utc,
        delivered_at_to=end_utc,
        limit=max(top_n * 3, top_n),
    )
    candidates = [
        XDraftCandidate(
            score=int(row["score"]),
            title=str(row["title"]),
            organization=str(row["organization"]),
            url=str(row["url"]),
            published_at=row["published_at"],
            fetched_at=str(row["fetched_at"]),
        )
        for row in rows
    ]
    content, item_count = build_x_post_content(
        post_date_jst=post_date_jst,
        candidates=candidates,
        top_n=top_n,
        lp_url=lp_url,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content + "\n", encoding="utf-8")

    store.record_x_draft(
        post_date_jst=post_date_jst,
        generated_at=generated_at,
        top_n=top_n,
        item_count=item_count,
        lp_url=lp_url,
        content=content,
        overwrite=force,
    )

    return XDraftResult(
        post_date_jst=post_date_jst,
        output_path=output_path,
        content=content,
        item_count=item_count,
        skipped=False,
    )
