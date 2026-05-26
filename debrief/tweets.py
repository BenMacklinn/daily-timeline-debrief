from __future__ import annotations

import html
import re
from urllib.parse import urlparse

import httpx

from debrief.models import Post, RowGroup, TweetContent

OEMBED_ENDPOINT = "https://publish.twitter.com/oembed"
VX_API_BASE = "https://api.vxtwitter.com"
MAX_TWEETS_PER_ROW = 12


def _handle_from_post(post: Post) -> str | None:
    if not post.tweet_link:
        return None
    path = urlparse(post.tweet_link).path.strip("/").split("/")
    if path:
        return f"@{path[0]}"
    return None


def normalize_tweet_url(url: str) -> str:
    """Strip tracking params; oEmbed prefers canonical status URLs."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 3 and parts[1] == "status":
        return f"https://twitter.com/{parts[0]}/status/{parts[2]}"
    return url.split("?")[0]


def parse_oembed_html(embed_html: str) -> str | None:
    """Extract tweet body text from Twitter oEmbed blockquote HTML."""
    unescaped = html.unescape(embed_html)
    match = re.search(r"<p[^>]*>(.*?)</p>", unescaped, re.DOTALL | re.IGNORECASE)
    if not match:
        return None

    paragraph = match.group(1)
    paragraph = re.sub(r"<a\b[^>]*>.*?</a>", "", paragraph, flags=re.DOTALL | re.IGNORECASE)
    paragraph = re.sub(r"<br\s*/?>", "\n", paragraph, flags=re.IGNORECASE)
    paragraph = re.sub(r"<[^>]+>", "", paragraph)
    text = html.unescape(paragraph).strip()
    return text or None


def fetch_tweet_oembed(tweet_url: str, *, timeout: float = 15.0) -> tuple[str | None, str | None]:
    """Return (tweet_text, author_handle) from Twitter oEmbed."""
    canonical = normalize_tweet_url(tweet_url)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(
                OEMBED_ENDPOINT,
                params={"url": canonical, "omit_script": "true"},
                headers={"User-Agent": "DailyTimelineDebrief/0.1"},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None, None

    text = parse_oembed_html(data.get("html", ""))
    author_url = data.get("author_url") or ""
    handle = None
    if author_url:
        path = urlparse(author_url).path.strip("/").split("/")
        if path:
            handle = f"@{path[0]}"
    return text, handle


def _tweet_id_and_handle(tweet_url: str) -> tuple[str, str] | None:
    canonical = normalize_tweet_url(tweet_url)
    parts = urlparse(canonical).path.strip("/").split("/")
    if len(parts) >= 3 and parts[1] == "status":
        return parts[0], parts[2]
    return None


def upgrade_twitter_media_url(url: str) -> str:
    """Request the largest available rendition from Twitter CDN URLs."""
    if not url or "pbs.twimg.com" not in url:
        return url
    if "/media/" in url:
        return f"{url.split('?', 1)[0]}?name=orig"
    if "card_img" in url and "name=" in url:
        return re.sub(r"name=[^&]+", "name=800x419", url)
    return url.split("?", 1)[0] if "/amplify_video_thumb/" in url else url


def fetch_tweet_embedded_media(
    tweet_url: str,
    *,
    timeout: float = 15.0,
) -> list[str]:
    """Return image URLs attached to a tweet (photos, link previews, video thumbnails)."""
    parsed = _tweet_id_and_handle(tweet_url)
    if parsed is None:
        return []
    handle, tweet_id = parsed

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(
                f"{VX_API_BASE}/{handle}/status/{tweet_id}",
                headers={"User-Agent": "DailyTimelineDebrief/0.1"},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    urls: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        if not raw:
            return
        cleaned = raw.strip()
        lower = cleaned.lower()
        if "pbs.twimg.com/card_img/" in lower or "/card_img/" in lower:
            return
        cleaned = upgrade_twitter_media_url(cleaned)
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        urls.append(cleaned)

    for item in data.get("media_extended") or []:
        media_type = item.get("type")
        if media_type == "image":
            add(item.get("url"))
        elif media_type == "video":
            add(item.get("thumbnail_url"))

    if not urls:
        for raw in data.get("mediaURLs") or []:
            if not raw or raw.endswith(".mp4"):
                continue
            lower = raw.lower()
            if "pbs.twimg.com/card_img/" in lower or "/card_img/" in lower:
                continue
            add(raw)

    return urls


def extract_tweets_from_row(group: RowGroup) -> list[TweetContent]:
    """Fetch tweet text via Twitter oEmbed (no vision, no API key)."""
    posts = [p for p in group.posts if p.type == "tweet" and p.tweet_link][
        :MAX_TWEETS_PER_ROW
    ]
    results: list[TweetContent] = []

    for post in posts:
        text, oembed_handle = fetch_tweet_oembed(post.tweet_link or "")
        results.append(
            TweetContent(
                slot=post.position.display,
                handle=oembed_handle or _handle_from_post(post),
                tweet_link=post.tweet_link,
                image_url=post.image_url or "",
                text=text,
            )
        )

    return results
