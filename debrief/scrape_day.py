from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from debrief.cache import load_scrape, save_scrape
from debrief.fetch import (
    default_date_pacific,
    fetch_timeline,
    group_posts_by_row,
    parse_date_to_iso,
    resolve_sheet_date,
)
from debrief.models import ResearchBundle, RowGroup, ScrapeCache
from debrief.render import write_preview
from debrief.research import research_row


@dataclass
class ScrapeDayResult:
    date: str
    date_iso: str
    scrape: ScrapeCache
    preview_path: Path


def scrape_rows(
    groups: list[RowGroup],
    date: str,
    *,
    skip_search: bool,
    skip_tweets: bool,
    search_provider: str,
    model: str,
    reasoning_effort: str,
) -> list[ResearchBundle]:
    bundles: list[ResearchBundle] = []
    for group in groups:
        tag_label = group.tag or "untagged"
        print(f"Researching row {group.label} ({tag_label})...")
        bundle = research_row(
            group,
            date,
            skip_search=skip_search,
            skip_tweets=skip_tweets,
            search_provider=search_provider,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        print(
            f"  → {len(bundle.tweets)} tweets, {len(bundle.articles)} articles, "
            f"{len(bundle.search_results)} search hits"
        )
        bundles.append(bundle)
    return bundles


def scrape_live_day(
    *,
    cache_base: Path,
    output_base: Path,
    skip_search: bool = False,
    skip_tweets: bool = False,
    search_provider: str = "tavily",
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
) -> ScrapeDayResult:
    """Fetch today's live timeline, research each row, save cache + preview."""
    folder_date = default_date_pacific()
    print(f"Fetching timeline sheet for {folder_date}...")
    timeline = fetch_timeline(date=folder_date)
    sheet_date = resolve_sheet_date(timeline, fallback=folder_date)
    date_iso = parse_date_to_iso(sheet_date)
    groups = group_posts_by_row(timeline.posts)
    if not groups:
        raise ValueError(
            f"No sorted story rows for {sheet_date}. "
            f"The dated sheet may not be published yet — "
            f"try again after rows are assigned on timeline.tbpn.com. "
            f"(API: https://timeline.tbpn.com/api/get-posts?date={sheet_date})"
        )

    print(f"Found {len(groups)} story rows ({timeline.count} total posts).\n")
    bundles = scrape_rows(
        groups,
        sheet_date,
        skip_search=skip_search,
        skip_tweets=skip_tweets,
        search_provider=search_provider,
        model=model,
        reasoning_effort=reasoning_effort,
    )

    cache_path = save_scrape(
        cache_base=cache_base,
        date=sheet_date,
        date_iso=date_iso,
        timeline=timeline,
        groups=groups,
        bundles=bundles,
        search_provider=search_provider,
        skip_search=skip_search,
    )
    print(f"\nSaved scrape cache → {cache_path.resolve()}")

    scrape = load_scrape(cache_base, date_iso)
    out_dir = output_base / date_iso
    has_debrief = (out_dir / "debrief.html").exists()
    preview_path = write_preview(scrape, out_dir, has_debrief=has_debrief)
    print(f"Preview → {preview_path.resolve()}")

    return ScrapeDayResult(
        date=sheet_date,
        date_iso=date_iso,
        scrape=scrape,
        preview_path=preview_path,
    )
