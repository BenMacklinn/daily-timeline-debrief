from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_PROXY_HOST_MARKERS = (
    "gettyimages.com",
    "media.gettyimages.com",
    "reutersconnect.com",
    "lookaside.fbsbx.com",
    "fbcdn.net",
)

_REFERER_BY_HOST = (
    ("gettyimages.com", "https://www.gettyimages.com/"),
    ("reutersconnect.com", "https://www.reutersconnect.com/"),
    ("reuters.com", "https://www.reuters.com/"),
    ("instagram.com", "https://www.instagram.com/"),
    ("facebook.com", "https://www.facebook.com/"),
)


def needs_image_proxy(url: str) -> bool:
    lower = url.lower()
    return any(marker in lower for marker in _PROXY_HOST_MARKERS)


def image_proxy_path(url: str) -> str:
    return f"/api/image-proxy?url={quote(url, safe='')}"


def display_image_url(url: str) -> str:
    if needs_image_proxy(url):
        return image_proxy_path(url)
    return url


def canonical_image_url(url: str) -> str:
    """Normalize stored, proxied, or encoded URLs for cache lookups."""
    raw = unquote(url).strip()
    if not raw:
        return raw
    if raw.startswith("/api/image-proxy"):
        parsed = urlparse(raw)
        inner = parse_qs(parsed.query).get("url", [""])[0]
        return unquote(inner).strip()
    if raw.startswith(("http://", "https://")):
        return raw
    return raw


def fetch_headers_for_url(url: str) -> dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    host = urlparse(url).netloc.lower()
    for marker, referer in _REFERER_BY_HOST:
        if marker in host:
            headers["Referer"] = referer
            break
    return headers


def extract_embedded_cdn_url(url: str) -> str | None:
    """Pull the underlying asset URL from dims-style CDN wrappers."""
    parsed = urlparse(url)
    embedded = parse_qs(parsed.query).get("url", [""])[0]
    if not embedded:
        return None
    embedded = unquote(embedded)
    if embedded.startswith(("http://", "https://")):
        return embedded
    return None


def upgrade_image_url(url: str) -> str:
    """Rewrite common CDN resize URLs to request a larger rendition."""
    embedded = extract_embedded_cdn_url(url)
    if embedded and any(
        marker in url.lower()
        for marker in ("dims.apnews.com", "brightspotcdn.com", "/dims4/")
    ):
        return embedded

    upgraded = url

    if "reuters.com/resizer/" in upgraded.lower():
        upgraded = re.sub(r"width=\d+", "width=2048", upgraded, flags=re.I)
        upgraded = re.sub(r"quality=\d+", "quality=90", upgraded, flags=re.I)

    if "image.cnbcfm.com" in upgraded and "w=" not in upgraded.lower():
        separator = "&" if "?" in upgraded else "?"
        upgraded = f"{upgraded}{separator}w=1920"

    if "365dm.com" in upgraded:
        upgraded = re.sub(
            r"/(\d+)x(\d+)/",
            lambda match: f"/{max(int(match.group(1)), 1600)}x{max(int(match.group(2)), 900)}/",
            upgraded,
        )

    if "cloudfront.net" in upgraded or "cloudinary.com" in upgraded:
        upgraded = re.sub(r"/w_\d+", "/w_1920", upgraded)
        upgraded = re.sub(r"/c_scale,w_\d+", "/c_scale,w_1920", upgraded)

    if "i.imgur.com" in upgraded:
        upgraded = re.sub(r"[smhl]\.(jpg|jpeg|png|webp)$", r".\1", upgraded, flags=re.I)

    return upgraded


def _is_dims_wrapper(url: str) -> bool:
    lower = url.lower()
    return "dims.apnews.com" in lower or ("/dims4/" in lower and "brightspotcdn.com" in lower)


def candidate_image_urls(url: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    embedded = extract_embedded_cdn_url(url)
    upgraded = upgrade_image_url(url)

    if embedded:
        add(embedded)
    add(upgraded)
    if not _is_dims_wrapper(url):
        add(url)
    return candidates


def validate_image_url(url: str, *, min_bytes: int = 15_000) -> bool:
    headers = fetch_headers_for_url(url)
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True) as client:
            response = client.head(url, headers=headers)
            if response.status_code >= 400:
                response = client.get(url, headers=headers)
            if response.status_code >= 400:
                return False

            content_type = (response.headers.get("content-type") or "").lower()
            if content_type and not content_type.startswith("image/"):
                return False

            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit():
                if int(content_length) < min_bytes:
                    return False
                return True

            if response.request.method == "HEAD":
                response = client.get(url, headers=headers)
            if response.status_code >= 400:
                return False
            content_type = (response.headers.get("content-type") or "").lower()
            if not content_type.startswith("image/"):
                return False
            return len(response.content) >= min_bytes
    except httpx.HTTPError:
        return False


_TRUSTED_IMAGE_HOST_MARKERS = (
    "apnews.com",
    "apimages.com",
    "reuters.com",
    "reutersmedia.net",
    "thomsonreuters.com",
    "gettyimages.com",
    "media.gettyimages.com",
    "epa.eu",
    "nytimes.com",
    "wsj.com",
    "bloomberg.com",
    "cnbcfm.com",
    "cloudfront.net",
    "cloudinary.com",
)


def _is_trusted_image_host(url: str) -> bool:
    lower = url.lower()
    return any(marker in lower for marker in _TRUSTED_IMAGE_HOST_MARKERS)


def resolve_fetchable_url(url: str) -> str | None:
    lower = url.lower()
    if "pbs.twimg.com" in lower:
        from debrief.tweets import upgrade_twitter_media_url

        return upgrade_twitter_media_url(url)

    embedded = extract_embedded_cdn_url(url)
    if embedded and _is_trusted_image_host(embedded):
        return upgrade_image_url(embedded)

    if _is_trusted_image_host(url):
        return upgrade_image_url(url)

    for candidate in candidate_image_urls(url):
        if validate_image_url(candidate):
            return candidate
    return None


def fetch_image_bytes(url: str) -> tuple[bytes, str] | None:
    headers = fetch_headers_for_url(url)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError:
        return None

    content_type = response.headers.get("content-type") or "application/octet-stream"
    if not content_type.lower().startswith("image/"):
        return None
    return response.content, content_type
