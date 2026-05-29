from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import httpx
import trafilatura

from debrief.image_urls import resolve_fetchable_url
from debrief.models import ArticleSnippet, ImageResult, Post, ResearchBundle, RowGroup, SearchSnippet, TweetContent
from debrief.query_enrichment import enrich_search_queries
from debrief.tweets import extract_tweets_from_row, fetch_tweet_embedded_media, normalize_tweet_url

MAX_ARTICLE_FETCHES = 3
MAX_SEARCH_QUERIES = 3
MAX_IMAGE_SEARCH_QUERIES = 3
MAX_TOPIC_IMAGES = 15
MAX_TWEET_MEDIA_WORKERS = 6
ARTICLE_TEXT_LIMIT = 4000
SEARCH_SNIPPET_LIMIT = 800

_JUNK_IMAGE_URL_PARTS = (
    "lookaside.instagram",
    "instagram.com/seo",
    "instagram.famm",
    "fbcdn.net",
    "lookaside.fbsbx",
    "fbsbx.com",
    "favicon",
    "/logo",
    "logo.",
    "avatar",
    "gravatar",
    "pixel.",
    "1x1",
    "emoji",
    "badge.",
    "/icons/",
    "spacer.",
    "transparent.gif",
    "data:image",
    "google_widget/crawler",
    "dealroom.co",
    "team-bhp.com/forum/attachments",
    "/forum/attachments/",
    "reutersconnect.com/item/",
    "reutersconnect.com/_next/image",
    "pbs.twimg.com/card_img/",
    "/card_img/",
    "brut.com",
    "brut.media",
    "cdninstagram.com",
    "scontent-",
)

_GENERIC_IMAGE_DESCRIPTIONS = frozenset({
    "image",
    "photo",
    "picture",
    "img",
    "thumbnail",
    "icon",
})

_DOCUMENT_QUERY_WORDS = re.compile(
    r"\b(S-1|SEC|filing|regulation|regulations|earnings|definition|explained|wiki|pdf|"
    r"authorization|verdict|deliberation|prospectus|registration statement|disclosure|"
    r"private company|underwriters?|Goldman|Morgan Stanley|Bank of America)\b",
    re.IGNORECASE,
)

_VISUAL_DESCRIPTION_WORDS = (
    "photo",
    "launch",
    "headquarters",
    "portrait",
    "satellite",
    "illustration",
    "diagram",
    "image of",
    "pictured",
    "rocket",
    "factory",
    "campus",
    "headshot",
    "screenshot",
)

_GENERIC_IMAGE_TERMS = frozenset({
    "photo", "photos", "images", "image", "diagram", "las", "vegas", "games", "game",
    "enhanced", "2026", "2025", "2024", "2023", "sunday", "monday", "friday", "saturday",
    "press", "conference", "swimming", "pool", "athlete", "united", "states", "competes",
    "final", "heat", "heats", "during", "after", "night", "sprint", "meter", "metres",
    "100m", "100", "men", "women", "file", "aug", "summer", "olympics",
})

_IRRELEVANT_IMAGE_MARKERS = (
    "pool house",
    "vacation rental",
    "airbnb",
    "heavenly relaxation",
    "mirage pool",
    "heart of lv",
    "rental",
    "house |",
    "swimming pool, games",
    "open house",
    "nyse",
    "trader works",
    "sports betting",
    "supreme court",
    "iranian",
    "anchorperson",
    "photo illustration",
    "rubber bands",
)

_SOCIAL_GRAPHIC_DESC_MARKERS = (
    "journalist:",
    "credits:",
    "live recap",
    "here's what to know",
    "what you need to know",
    "backed by donald",
    "encourages athletes to use performance",
    "highly controversial",
    "#enhancedgames",
    "via reuters connect",
    "swipe for",
)

_LOW_RES_URL_MARKERS = (
    "thumbnail",
    "thumb/",
    "_thumb",
    "-thumb",
    "150x150",
    "200x200",
    "300x200",
    "320x240",
    "resize/59",
    "resize/12",
    "resize/15",
    "resize/20",
    "resize/30",
    "resize/40",
    "resize/50",
    "w=100",
    "w=150",
    "w=200",
    "w=300",
    "width=100",
    "height=100",
    "small",
    "preview",
    "s=612x612",
    "/attachments/",
    "4445t-",
    "5280t-",
)

_HIGH_QUALITY_SOURCES = (
    "apnews.com",
    "apimages.com",
    "reuters.com",
    "reutersmedia.net",
    "gettyimages.com",
    "media.gettyimages.com",
    "epa.eu",
    "nytimes.com",
    "wsj.com",
    "bloomberg.com",
    "shutterstock.com",
    "cnbcfm.com",
)

_WIRE_PHOTO_MARKERS = (
    "ap photo",
    "reuters",
    "getty images",
    "epa",
    "licensable picture",
)
def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def fetch_article_text(url: str, *, timeout: float = 20.0) -> ArticleSnippet | None:
    """Download and extract main article text."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; DailyTimelineDebrief/0.1; +research bot)"
                    )
                },
            )
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError:
        return None

    extracted = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        output_format="txt",
    )
    if not extracted or len(extracted.strip()) < 80:
        return None

    metadata = trafilatura.extract_metadata(html, default_url=url)
    title = metadata.title if metadata else None
    return ArticleSnippet(
        url=url,
        title=title,
        text=_truncate(extracted.strip(), ARTICLE_TEXT_LIMIT),
    )


def build_fallback_queries(group: RowGroup, date: str) -> list[str]:
    """Legacy fallback if GPT query enrichment is unavailable."""
    queries: list[str] = []
    tag = group.tag
    date_parts = date.split("-")
    readable_date = ""
    if len(date_parts) == 3:
        month, day, year = date_parts
        readable_date = f"{month}/{day}/{year}"

    if tag and tag not in {"misc", "trial"}:
        queries.append(f"{tag} news {readable_date or date}")
    if group.handles:
        top_handle = group.handles[0].lstrip("@")
        queries.append(f"{top_handle} {readable_date or date}")
    elif tag:
        queries.append(f"{tag} news {readable_date or date}")

    return queries[:MAX_SEARCH_QUERIES] or [f"tech news {readable_date or date}"]


_BLOCKED_IMAGE_QUERY_FRAGMENTS = (
    "swimming pool",
    "pool house",
    "pool photos",
    "las vegas photos",
    "las vegas images",
)


def _sanitize_image_description(description: str | None) -> str | None:
    if not description:
        return None
    cleaned = re.sub(r"\s+", " ", description.strip())
    if cleaned.lower() in _GENERIC_IMAGE_DESCRIPTIONS:
        return None
    return cleaned


def _url_quality_score(url: str) -> int:
    lower = url.lower()
    score = 0

    if any(source in lower for source in _HIGH_QUALITY_SOURCES):
        score += 10

    resize = re.search(r"resize/(\d+)x(\d+)", lower)
    if resize:
        width = int(resize.group(1))
        height = int(resize.group(2))
        score += min(max(width, height) // 80, 24)
        if max(width, height) < 700:
            score -= 12

    crop = re.search(r"crop/(\d+)x(\d+)", lower)
    if crop:
        score += min(int(crop.group(1)) // 250, 18)

    inline = re.search(r"/(\d{3,4})x(\d{3,4})/", lower)
    if inline:
        score += min(max(int(inline.group(1)), int(inline.group(2))) // 100, 14)

    if any(marker in lower for marker in _LOW_RES_URL_MARKERS):
        score -= 14

    if lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif")):
        score += 2

    return score


def _enhance_queries_for_quality(queries: list[str]) -> list[str]:
    if not queries:
        return queries
    enhanced = list(queries)
    first = enhanced[0].lower()
    if any(token in first for token in ("reuters", "ap photo", "getty", "high resolution")):
        return enhanced
    if re.search(r"\bphotos?\b", first) and not re.search(r"\b(ap|reuters|getty)\b", first):
        enhanced[0] = re.sub(r"\bphotos\b", "photos AP Reuters", enhanced[0], count=1, flags=re.I)
        enhanced[0] = re.sub(r"\bphoto\b", "photo AP Reuters", enhanced[0], count=1, flags=re.I)
    return enhanced


def _normalize_image_url(url: str) -> str:
    """Identity key for deduping — prefer source crop over resize variant."""
    parsed = urlparse(url)
    lower = url.lower()
    crop = re.search(r"crop/(\d+x\d+)", lower)
    if crop and "apnews.com" in parsed.netloc.lower():
        return f"ap:{crop.group(1)}"
    return f"{parsed.netloc.lower()}{parsed.path.lower()}"


def _description_fingerprint(description: str | None) -> str:
    if not description:
        return ""
    text = re.sub(r"\s+", " ", description.lower().strip())
    text = re.sub(r"\(ap photo/[^)]+\)", "", text)
    text = re.sub(r"\b\d{1,2}/\d{1,2}/\d{4}\b", "", text)
    return text[:160]


def _filter_image_queries(
    queries: list[str],
    *,
    topic_summary: str | None,
    key_entities: list[str] | None = None,
) -> list[str]:
    topic_lower = (topic_summary or "").lower()
    entity_blob = " ".join(key_entities or []).lower()
    swim_topic = any(token in topic_lower for token in ("swim", "aquatic", "pool event"))

    filtered: list[str] = []
    for query in queries:
        lower = query.lower()
        if not swim_topic and any(fragment in lower for fragment in _BLOCKED_IMAGE_QUERY_FRAGMENTS):
            continue
        if "trump" in lower and "trump" not in topic_lower and "trump" not in entity_blob:
            continue
        filtered.append(query)

    return filtered or queries[:2]


def _event_first_image_queries(
    *,
    row_tag: str | None,
    image_queries: list[str],
    max_queries: int = MAX_IMAGE_SEARCH_QUERIES,
) -> list[str]:
    """Prefer general event/topic queries over person-specific cached GPT queries."""
    merged: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        normalized = query.strip()
        if not normalized:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        merged.append(normalized)

    tag = (row_tag or "").strip()
    for query in image_queries:
        add(query)

    if tag and tag.lower() not in {"misc", "none", "(none)"} and len(merged) < max_queries:
        add(f"{tag} 2026 event photos")

    return merged[:max_queries]


def _prepare_tavily_image_queries(
    *,
    row_tag: str | None,
    image_queries: list[str] | None,
    search_queries: list[str] | None,
    topic_summary: str | None,
    key_entities: list[str] | None = None,
) -> tuple[list[str], str, int] | None:
    """Return (queries, search_depth, max_results) for Tavily image search."""
    queries = [q.strip() for q in (image_queries or []) if q.strip()]
    if not queries:
        queries = build_fallback_image_queries(
            topic_summary=topic_summary,
            search_queries=search_queries or [],
        )

    queries = _event_first_image_queries(
        row_tag=row_tag,
        image_queries=queries,
        max_queries=MAX_IMAGE_SEARCH_QUERIES,
    )
    queries = _filter_image_queries(
        queries,
        topic_summary=topic_summary,
        key_entities=key_entities,
    )
    queries = _enhance_queries_for_quality(queries)
    if not queries and topic_summary:
        queries = [f"{topic_summary.strip()} photo"]
    if not queries:
        return None

    return queries[:MAX_IMAGE_SEARCH_QUERIES], "advanced", 10


def _anchor_phrases(
    *,
    topic_summary: str | None,
    queries: list[str],
    key_entities: list[str] | None = None,
) -> list[str]:
    phrases: list[str] = []

    for entity in key_entities or []:
        cleaned = entity.strip().lower()
        if len(cleaned) >= 4 and cleaned not in _GENERIC_IMAGE_TERMS:
            phrases.append(cleaned)

    for q in queries:
        cleaned = re.sub(r"\b(photo|photos|images|image|diagram)\b", "", q, flags=re.I)
        cleaned = re.sub(r"\b\d{4}\b", "", cleaned)
        words = [
            word.lower()
            for word in cleaned.split()
            if len(word) > 2 and word.lower() not in _GENERIC_IMAGE_TERMS
        ]
        for size in (3, 2):
            for i in range(len(words) - size + 1):
                phrase = " ".join(words[i : i + size])
                if len(phrase) >= 8:
                    phrases.append(phrase)

    if topic_summary:
        for match in re.findall(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)+)\b", topic_summary):
            phrases.append(match.lower())

    deduped: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        if phrase in seen:
            continue
        seen.add(phrase)
        deduped.append(phrase)
    return deduped


def _mandatory_subject_terms(
    *,
    topic_summary: str | None,
    queries: list[str],
    key_entities: list[str] | None = None,
) -> list[str]:
    """Reserved for future person-centric rows; event rows should not hard-filter here."""
    return []


def _required_match_terms(
    *,
    topic_summary: str | None,
    queries: list[str],
    key_entities: list[str] | None = None,
    row_tag: str | None = None,
) -> list[str]:
    """Terms that should appear in an image when the topic names them explicitly."""
    terms: list[str] = []
    for entity in key_entities or []:
        cleaned = entity.strip().lower()
        if len(cleaned) >= 4 and cleaned not in _GENERIC_IMAGE_TERMS:
            terms.append(cleaned)

    tag = (row_tag or "").strip().lower()
    if tag and tag not in _GENERIC_IMAGE_TERMS and tag not in {"misc", "none"}:
        terms.append(tag)

    blob = " ".join([topic_summary or "", *queries]).lower()
    if "enhanced games" in blob:
        terms.append("enhanced games")

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def _is_relevant_image(
    description: str | None,
    url: str,
    *,
    anchor_phrases: list[str],
    topic_summary: str | None,
    queries: list[str],
    key_entities: list[str] | None = None,
    row_tag: str | None = None,
) -> bool:
    """Loose topic match — producers curate bad hits with the Research UI remove button."""
    blob = f"{description or ''} {url}".lower()
    if any(marker in blob for marker in _IRRELEVANT_IMAGE_MARKERS):
        return False

    required = _required_match_terms(
        topic_summary=topic_summary,
        queries=queries,
        key_entities=key_entities,
        row_tag=row_tag,
    )
    if required and any(term in blob for term in required):
        return True

    for entity in key_entities or []:
        cleaned = entity.strip().lower()
        if len(cleaned) >= 4 and cleaned not in _GENERIC_IMAGE_TERMS and cleaned in blob:
            return True

    for phrase in anchor_phrases:
        if len(phrase) >= 6 and phrase in blob:
            return True

    for match in re.findall(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b", topic_summary or ""):
        name = match.lower()
        if name in {"las vegas", "the enhanced", "united states"}:
            continue
        if name in blob:
            return True

    desc_lower = (description or "").lower()
    if desc_lower and any(marker in desc_lower for marker in _WIRE_PHOTO_MARKERS):
        strong_terms = [
            term for term in _topic_terms(topic_summary, queries) if term not in _GENERIC_IMAGE_TERMS
        ]
        if any(term in blob for term in strong_terms if len(term) >= 5):
            return True

    strong_terms = [
        term for term in _topic_terms(topic_summary, queries) if term not in _GENERIC_IMAGE_TERMS
    ]
    hits = [term for term in strong_terms if term in blob]
    return bool(hits) and any(len(term) >= 5 for term in hits)


def _strip_document_words(text: str) -> str:
    cleaned = _DOCUMENT_QUERY_WORDS.sub("", text).strip()
    return re.sub(r"\s+", " ", cleaned)


def build_fallback_image_queries(
    *,
    topic_summary: str | None,
    search_queries: list[str],
) -> list[str]:
    """Heuristic slideshow queries when GPT image_queries are unavailable."""
    queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        normalized = query.strip()
        if not normalized:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        queries.append(normalized)

    for query in search_queries[:MAX_IMAGE_SEARCH_QUERIES]:
        visual = _strip_document_words(query)
        if not visual:
            continue
        lower = visual.lower()
        if not any(token in lower for token in ("photo", "photos", "image", "images", "diagram")):
            visual = f"{visual} photo"
        add(visual)

    if len(queries) < 2 and topic_summary:
        short = _strip_document_words(topic_summary)
        short = re.sub(r"[^\w\s'-]", " ", short)
        words = [word for word in short.split() if len(word) > 2][:6]
        if words:
            add(f"{' '.join(words)} photo")

    return queries[:MAX_IMAGE_SEARCH_QUERIES]


def _looks_like_headline_graphic_caption(description: str | None) -> bool:
    """Detect article/social promo captions — not wire-photo alt text."""
    if not description:
        return False
    lower = description.lower().strip()
    if any(marker in lower for marker in _SOCIAL_GRAPHIC_DESC_MARKERS):
        return True
    if lower.count("#") >= 2:
        return True
    if re.search(r":\s*(press conference|live recap|what to know|read more)\b", lower):
        return True
    if len(description) > 90 and "photo" not in lower and "ap photo" not in lower:
        if description.endswith(".") and description.count(" ") >= 12:
            return True
    return False


def _is_slideshow_image(url: str, description: str | None = None) -> bool:
    url_lower = url.lower()
    if not url_lower.startswith(("http://", "https://")):
        return False
    if "dims.apnews.com" in url_lower:
        return False
    if "/dims4/" in url_lower and "brightspotcdn.com" in url_lower:
        return False
    if any(part in url_lower for part in _JUNK_IMAGE_URL_PARTS):
        return False
    if _url_quality_score(url) < -8:
        return False
    if description:
        desc_lower = description.lower()
        if any(
            token in desc_lower
            for token in ("favicon", "logo", "icon", "registration statement", "form s-1")
        ):
            return False
        if _looks_like_headline_graphic_caption(description):
            if not any(marker in desc_lower for marker in _WIRE_PHOTO_MARKERS):
                return False
    return True


def _topic_terms(topic_summary: str | None, queries: list[str]) -> set[str]:
    blob = " ".join(part for part in [topic_summary, *queries] if part)
    terms: set[str] = set()
    for word in re.findall(r"[A-Za-z0-9']{4,}", blob):
        terms.add(word.lower())
    return terms


def _image_rank(
    url: str,
    description: str | None,
    *,
    topic_terms: set[str],
    topic_summary: str | None,
) -> int:
    if not _is_slideshow_image(url, description):
        return -100

    score = 0
    score += _url_quality_score(url)
    if description:
        desc_lower = description.lower()
        score += sum(2 for word in _VISUAL_DESCRIPTION_WORDS if word in desc_lower)
        score += sum(3 for term in topic_terms if term in desc_lower and term not in _GENERIC_IMAGE_TERMS)
        score += sum(4 for marker in _WIRE_PHOTO_MARKERS if marker in desc_lower)
        if "2024" in desc_lower and topic_summary and "2026" in topic_summary:
            score -= 4
        if "file)" in desc_lower or "file photo" in desc_lower:
            score -= 2
    return score


def tavily_search(query: str, *, max_results: int = 5) -> list[SearchSnippet]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_answer=False,
        )
    except Exception:
        return []

    snippets: list[SearchSnippet] = []
    for result in response.get("results", []):
        content = result.get("content") or result.get("raw_content") or ""
        if not content:
            continue
        snippets.append(
            SearchSnippet(
                query=query,
                title=result.get("title") or result.get("url", ""),
                url=result.get("url", ""),
                content=_truncate(content.strip(), SEARCH_SNIPPET_LIMIT),
            )
        )
    return snippets


def duckduckgo_search(query: str, *, max_results: int = 5) -> list[SearchSnippet]:
    snippets: list[SearchSnippet] = []
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []

    for result in results:
        body = result.get("body") or ""
        if not body:
            continue
        snippets.append(
            SearchSnippet(
                query=query,
                title=result.get("title") or result.get("href", ""),
                url=result.get("href", ""),
                content=_truncate(body.strip(), SEARCH_SNIPPET_LIMIT),
            )
        )
    return snippets


def _image_from_payload(
    payload: str | dict,
    *,
    source_url: str | None = None,
) -> ImageResult | None:
    if isinstance(payload, str):
        url = payload.strip()
        if not url:
            return None
        return ImageResult(url=url, source_url=source_url)

    url = (payload.get("url") or "").strip()
    if not url:
        return None
    description = payload.get("description") or payload.get("title")
    cleaned = description.strip() if isinstance(description, str) and description.strip() else None
    return ImageResult(
        url=url,
        description=_sanitize_image_description(cleaned),
        source_url=source_url,
    )


def _tweet_image_description(text: str | None) -> str | None:
    if not text:
        return None
    first_line = text.strip().splitlines()[0].strip()
    return _sanitize_image_description(first_line[:160] if first_line else None)


def _collect_tweet_embedded_images(
    *,
    tweets: list[TweetContent] | None = None,
    posts: list[Post] | None = None,
) -> list[ImageResult]:
    """Gather media URLs embedded in row tweets (not TBPN timeline screenshots)."""
    sources: list[tuple[str, str | None]] = []
    seen_links: set[str] = set()

    def add_source(tweet_link: str | None, text: str | None) -> None:
        if not tweet_link:
            return
        key = normalize_tweet_url(tweet_link)
        if key in seen_links:
            return
        seen_links.add(key)
        sources.append((tweet_link, text))

    for tweet in tweets or []:
        add_source(tweet.tweet_link, tweet.text)

    for post in posts or []:
        if post.type == "tweet":
            add_source(post.tweet_link, None)

    candidates: list[ImageResult] = []
    seen_urls: set[str] = set()

    if not sources:
        return candidates

    workers = min(MAX_TWEET_MEDIA_WORKERS, len(sources))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_tweet_embedded_media, tweet_link): (tweet_link, text)
            for tweet_link, text in sources
        }
        for future in as_completed(futures):
            tweet_link, text = futures[future]
            try:
                media_urls = future.result()
            except Exception:
                continue
            for url in media_urls:
                normalized = url.lower().split("?", 1)[0]
                if normalized in seen_urls:
                    continue
                seen_urls.add(normalized)
                candidates.append(
                    ImageResult(
                        url=url,
                        description=_tweet_image_description(text),
                        source_url=tweet_link,
                    )
                )

    return candidates


def _finalize_ranked_images(ranked: list[tuple[int, ImageResult]]) -> list[ImageResult]:
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [image for _, image in ranked][:MAX_TOPIC_IMAGES]


def fetch_topic_images(
    *,
    topic_summary: str | None,
    image_queries: list[str] | None = None,
    search_queries: list[str] | None = None,
    key_entities: list[str] | None = None,
    tweets: list[TweetContent] | None = None,
    posts: list[Post] | None = None,
    row_tag: str | None = None,
) -> list[ImageResult]:
    """Return validated visuals — tweet media first; Tavily only when needed."""
    api_key = os.getenv("TAVILY_API_KEY")

    seen_urls: set[str] = set()
    seen_descriptions: set[str] = set()
    ranked: list[tuple[int, ImageResult]] = []

    def consider_timeline(image: ImageResult, *, order: int) -> None:
        if len(ranked) >= MAX_TOPIC_IMAGES:
            return
        resolved_url = resolve_fetchable_url(image.url)
        if not resolved_url:
            return

        image = ImageResult(
            url=resolved_url,
            description=_sanitize_image_description(image.description),
            source_url=image.source_url,
        )

        normalized_url = _normalize_image_url(image.url)
        if normalized_url in seen_urls:
            return
        if not _is_slideshow_image(image.url, image.description):
            return

        seen_urls.add(normalized_url)
        ranked.append((300 - order, image))

    for order, tweet_image in enumerate(_collect_tweet_embedded_images(tweets=tweets, posts=posts)):
        consider_timeline(tweet_image, order=order)

    tavily_plan = _prepare_tavily_image_queries(
        row_tag=row_tag,
        image_queries=image_queries,
        search_queries=search_queries,
        topic_summary=topic_summary,
        key_entities=key_entities,
    )

    if tavily_plan is None or not api_key:
        return _finalize_ranked_images(ranked)

    if len(ranked) >= MAX_TOPIC_IMAGES:
        return _finalize_ranked_images(ranked)

    queries, search_depth, max_results = tavily_plan
    topic_terms = _topic_terms(topic_summary, queries)
    anchor_phrases = _anchor_phrases(
        topic_summary=topic_summary,
        queries=queries,
        key_entities=key_entities,
    )

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
    except Exception:
        return _finalize_ranked_images(ranked)

    def consider(payload: str | dict, *, source_url: str | None = None, source_bias: int = 0) -> None:
        if len(ranked) >= MAX_TOPIC_IMAGES:
            return
        image = _image_from_payload(payload, source_url=source_url)
        if image is None:
            return

        resolved_url = resolve_fetchable_url(image.url)
        if not resolved_url:
            return

        image = ImageResult(
            url=resolved_url,
            description=_sanitize_image_description(image.description),
            source_url=image.source_url,
        )

        normalized_url = _normalize_image_url(image.url)
        fingerprint = _description_fingerprint(image.description)
        if normalized_url in seen_urls:
            return
        if fingerprint and fingerprint in seen_descriptions:
            return
        if not _is_relevant_image(
            image.description,
            image.url,
            anchor_phrases=anchor_phrases,
            topic_summary=topic_summary,
            queries=queries,
            key_entities=key_entities,
            row_tag=row_tag,
        ):
            return

        score = _image_rank(
            image.url,
            image.description,
            topic_terms=topic_terms,
            topic_summary=topic_summary,
        )
        if score < 0:
            return

        seen_urls.add(normalized_url)
        if fingerprint:
            seen_descriptions.add(fingerprint)
        ranked.append((score + source_bias, image))

    for query in queries:
        if len(ranked) >= MAX_TOPIC_IMAGES:
            break
        try:
            response = client.search(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                include_images=True,
                include_image_descriptions=True,
            )
        except Exception:
            continue

        for result in response.get("results", []):
            if len(ranked) >= MAX_TOPIC_IMAGES:
                break
            source_url = result.get("url")
            for payload in result.get("images", []):
                consider(payload, source_url=source_url, source_bias=1)
                if len(ranked) >= MAX_TOPIC_IMAGES:
                    break

        if len(ranked) >= MAX_TOPIC_IMAGES:
            break
        for payload in response.get("images", []):
            consider(payload, source_bias=0)
            if len(ranked) >= MAX_TOPIC_IMAGES:
                break

    return _finalize_ranked_images(ranked)


def search_web(
    query: str,
    *,
    provider: str = "tavily",
    max_results: int = 5,
) -> list[SearchSnippet]:
    if provider == "tavily":
        results = tavily_search(query, max_results=max_results)
        if results:
            return results
        return duckduckgo_search(query, max_results=max_results)
    if provider == "duckduckgo":
        return duckduckgo_search(query, max_results=max_results)
    return []


def build_post_summary(group: RowGroup, tweets: list | None = None) -> str:
    tweet_by_slot = {t.slot: t for t in (tweets or [])}
    lines: list[str] = []
    for post in group.posts:
        handle = ""
        if post.tweet_link:
            path = urlparse(post.tweet_link).path.strip("/").split("/")
            if path:
                handle = f"@{path[0]}"
        link = post.tweet_link or post.article_link or post.image_url or ""
        line = (
            f"- {post.position.display} ({post.received_at_pacific}) "
            f"[{post.type}] {handle} {link}".strip()
        )
        tweet = tweet_by_slot.get(post.position.display)
        if tweet and tweet.text:
            line += f"\n  Tweet: {tweet.text}"
        lines.append(line)
    return "\n".join(lines)


def research_row(
    group: RowGroup,
    date: str,
    *,
    skip_search: bool = False,
    skip_tweets: bool = False,
    search_provider: str = "tavily",
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
) -> ResearchBundle:
    """Gather tweet text via oEmbed, enrich queries, then search."""
    articles: list[ArticleSnippet] = []
    for url in group.article_urls[:MAX_ARTICLE_FETCHES]:
        snippet = fetch_article_text(url)
        if snippet:
            articles.append(snippet)

    tweets = []
    if not skip_tweets:
        print(f"  Fetching tweet content (oEmbed) for row {group.label}...")
        tweets = extract_tweets_from_row(group)
        read = sum(1 for t in tweets if t.text)
        print(f"  → {read}/{len(tweets)} tweets fetched")

    topic_summary: str | None = None
    search_queries: list[str] = []
    image_queries: list[str] = []
    key_entities: list[str] = []
    search_results: list[SearchSnippet] = []

    if not skip_search:
        if os.getenv("OPENAI_API_KEY"):
            print(f"  Enriching search queries for row {group.label}...")
            try:
                plan = enrich_search_queries(
                    group,
                    date,
                    tweets=tweets,
                    articles=articles,
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
                topic_summary = plan.topic_summary
                search_queries = plan.search_queries[:MAX_SEARCH_QUERIES]
                image_queries = plan.image_queries[:MAX_IMAGE_SEARCH_QUERIES]
                key_entities = plan.key_entities
                print(f"  → Topic: {topic_summary}")
                for q in search_queries:
                    print(f"  → Query: {q}")
                for q in image_queries:
                    print(f"  → Image query: {q}")
            except Exception as exc:
                print(f"  → Query enrichment failed ({exc}), using fallback")
                search_queries = build_fallback_queries(group, date)
                image_queries = build_fallback_image_queries(
                    topic_summary=group.tag,
                    search_queries=search_queries,
                )
        else:
            search_queries = build_fallback_queries(group, date)
            image_queries = build_fallback_image_queries(
                topic_summary=group.tag,
                search_queries=search_queries,
            )

        seen_urls: set[str] = set()
        for query in search_queries:
            for snippet in search_web(query, provider=search_provider):
                if snippet.url in seen_urls:
                    continue
                seen_urls.add(snippet.url)
                search_results.append(snippet)

    return ResearchBundle(
        row=group.label,
        tag=group.tag,
        handles=group.handles,
        tweets=tweets,
        topic_summary=topic_summary,
        search_queries_used=search_queries,
        image_queries_used=image_queries,
        key_entities=key_entities,
        articles=articles,
        search_results=search_results,
        post_summary=build_post_summary(group, tweets),
    )
