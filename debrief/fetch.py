from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from debrief.models import Post, RowGroup, TimelineResponse

API_BASE = "https://timeline.tbpn.com/api/get-posts"
PACIFIC = ZoneInfo("America/Los_Angeles")


def default_date_pacific() -> str:
    """Return today's date as MM-DD-YYYY in Pacific time."""
    now = datetime.now(PACIFIC)
    return now.strftime("%m-%d-%Y")


def parse_date_to_iso(date_str: str) -> str:
    """Convert MM-DD-YYYY to YYYY-MM-DD."""
    parsed = datetime.strptime(date_str, "%m-%d-%Y")
    return parsed.strftime("%Y-%m-%d")


def resolve_sheet_date(timeline: TimelineResponse, *, fallback: str) -> str:
    """Return MM-DD-YYYY from the API when present, else fallback."""
    raw = (timeline.date or "").strip()
    if not raw:
        return fallback
    if len(raw) == 10 and raw[4] == "-":
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%m-%d-%Y")
    return raw


def extract_handle(url: str | None) -> str | None:
    if not url or "x.com/" not in url and "twitter.com/" not in url:
        return None
    path = urlparse(url).path.strip("/").split("/")
    if path:
        return f"@{path[0]}"
    return None


def timeline_api_url(date: str) -> str:
    """Return the get-posts URL for a sheet day (MM-DD-YYYY)."""
    return f"{API_BASE}?date={date}"


def fetch_timeline(*, date: str | None = None, timeout: float = 30.0) -> TimelineResponse:
    """Fetch the timeline sheet for a day (default: today in Pacific, MM-DD-YYYY)."""
    sheet_date = date or default_date_pacific()
    url = timeline_api_url(sheet_date)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return TimelineResponse.model_validate(response.json())


def group_posts_by_row(posts: list[Post]) -> list[RowGroup]:
    """Group sorted sheet posts by row label."""
    row_posts: dict[str, list[Post]] = {}

    for post in posts:
        if post.position.type != "row" or not post.label:
            continue
        row_posts.setdefault(post.label, []).append(post)

    groups: list[RowGroup] = []
    for label in sorted(row_posts.keys()):
        items = sorted(row_posts[label], key=lambda p: p.position.position)
        tags = [p.tag for p in items if p.tag]
        tag = tags[0] if tags else None

        handles: list[str] = []
        article_urls: list[str] = []
        for post in items:
            handle = extract_handle(post.tweet_link)
            if handle and handle not in handles:
                handles.append(handle)
            if post.article_link and post.article_link not in article_urls:
                article_urls.append(post.article_link)

        iso_times = sorted(p.received_at for p in items)
        time_by_iso = {p.received_at: p.received_at_pacific for p in items}
        groups.append(
            RowGroup(
                label=label,
                tag=tag,
                posts=items,
                handles=handles,
                article_urls=article_urls,
                time_start=time_by_iso[iso_times[0]] if iso_times else None,
                time_end=time_by_iso[iso_times[-1]] if iso_times else None,
            )
        )

    return groups


def summarize_row(group: RowGroup) -> str:
    """One-line summary for dry-run output."""
    tag = group.tag or "untagged"
    handles = ", ".join(group.handles[:5])
    if len(group.handles) > 5:
        handles += f" (+{len(group.handles) - 5} more)"
    return (
        f"Row {group.label} [{tag}] — {len(group.posts)} items, "
        f"{group.time_start} → {group.time_end} | {handles or 'no handles'}"
    )
