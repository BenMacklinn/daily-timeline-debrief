from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from debrief.models import ImageResult, ResearchBundle, RowGroup, ScrapeCache, TimelineResponse

CACHE_VERSION = 3


def cache_dir_for_date(base: Path, date_iso: str) -> Path:
    return base / date_iso


def scrape_cache_path(base: Path, date_iso: str) -> Path:
    return cache_dir_for_date(base, date_iso) / "scrape.json"


def save_scrape(
    *,
    cache_base: Path,
    date: str,
    date_iso: str,
    timeline: TimelineResponse,
    groups: list[RowGroup],
    bundles: list[ResearchBundle],
    search_provider: str,
    skip_search: bool,
) -> Path:
    if len(groups) != len(bundles):
        raise ValueError("groups and bundles must have the same length")

    payload = ScrapeCache(
        version=CACHE_VERSION,
        date=date,
        date_iso=date_iso,
        scraped_at=datetime.now(),
        post_count=timeline.count,
        search_provider=search_provider,
        skip_search=skip_search,
        timeline=timeline,
        rows=[
            {"group": group, "research": bundle}
            for group, bundle in zip(groups, bundles, strict=True)
        ],
    )

    path = scrape_cache_path(cache_base, date_iso)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_scrape(cache_base: Path, date_iso: str) -> ScrapeCache:
    path = scrape_cache_path(cache_base, date_iso)
    if not path.exists():
        raise FileNotFoundError(f"No cached scrape at {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    return ScrapeCache.model_validate(data)


def scrape_exists(cache_base: Path, date_iso: str) -> bool:
    return scrape_cache_path(cache_base, date_iso).exists()


def find_latest_scrape(cache_base: Path) -> ScrapeCache | None:
    latest_path: Path | None = None
    latest_mtime = 0.0
    if not cache_base.is_dir():
        return None
    for path in cache_base.glob("*/scrape.json"):
        mtime = path.stat().st_mtime
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_path = path
    if latest_path is None:
        return None
    return ScrapeCache.model_validate(
        json.loads(latest_path.read_text(encoding="utf-8"))
    )


def resolve_serve_date(cache_base: Path, *, folder_date: str | None) -> tuple[str, str]:
    """Pick which day --serve should load when --date is omitted."""
    from debrief.fetch import default_date_pacific, parse_date_to_iso

    if folder_date:
        return folder_date, parse_date_to_iso(folder_date)

    today = default_date_pacific()
    today_iso = parse_date_to_iso(today)
    if scrape_exists(cache_base, today_iso):
        return today, today_iso

    latest = find_latest_scrape(cache_base)
    if latest is not None:
        return latest.date, latest.date_iso

    return today, today_iso


def update_row_images(
    cache_base: Path,
    date_iso: str,
    row_label: str,
    images: list[ImageResult],
) -> ScrapeCache:
    scrape = load_scrape(cache_base, date_iso)
    updated = False
    for row in scrape.rows:
        if row.group.label == row_label:
            row.research.images = images
            updated = True
            break
    if not updated:
        raise KeyError(f"No row {row_label!r} in cache for {date_iso}")

    path = scrape_cache_path(cache_base, date_iso)
    path.write_text(
        json.dumps(scrape.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return scrape


def remove_row_image(
    cache_base: Path,
    date_iso: str,
    row_label: str,
    image_url: str,
) -> ScrapeCache:
    scrape = load_scrape(cache_base, date_iso)
    updated = False
    for row in scrape.rows:
        if row.group.label != row_label:
            continue
        before = len(row.research.images)
        row.research.images = [
            image for image in row.research.images if image.url != image_url
        ]
        if len(row.research.images) == before:
            raise KeyError(f"Image not found in row {row_label!r}")
        updated = True
        break
    if not updated:
        raise KeyError(f"No row {row_label!r} in cache for {date_iso}")

    path = scrape_cache_path(cache_base, date_iso)
    path.write_text(
        json.dumps(scrape.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return scrape


def remove_row_image(
    cache_base: Path,
    date_iso: str,
    row_label: str,
    image_url: str,
) -> ScrapeCache:
    scrape = load_scrape(cache_base, date_iso)
    updated = False
    for row in scrape.rows:
        if row.group.label != row_label:
            continue
        before = len(row.research.images)
        row.research.images = [
            image for image in row.research.images if image.url != image_url
        ]
        if len(row.research.images) == before:
            raise KeyError(f"Image not found in row {row_label!r}")
        updated = True
        break
    if not updated:
        raise KeyError(f"No row {row_label!r} in cache for {date_iso}")

    path = scrape_cache_path(cache_base, date_iso)
    path.write_text(
        json.dumps(scrape.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return scrape
