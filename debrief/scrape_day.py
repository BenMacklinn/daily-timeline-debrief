from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from debrief.cache import load_scrape, save_scrape
from debrief.fetch import (
    default_date_pacific,
    fetch_timeline,
    group_posts_by_row,
    parse_date_to_iso,
    resolve_sheet_date,
    timeline_api_url,
)
from debrief.models import ResearchBundle, RowGroup, ScrapeCache, TimelineResponse
from debrief.research import build_post_summary, research_row


@dataclass
class ScrapeDayResult:
    date: str
    date_iso: str
    scrape: ScrapeCache
    preview_path: Path | None
    researched_rows: int


def _timeline_sheet_or_error(*, folder_date: str | None = None) -> tuple[TimelineResponse, str, str, list[RowGroup]]:
    folder_date = folder_date or default_date_pacific()
    timeline = fetch_timeline(date=folder_date)
    sheet_date = resolve_sheet_date(timeline, fallback=folder_date)
    date_iso = parse_date_to_iso(sheet_date)
    groups = group_posts_by_row(timeline.posts)
    if not groups:
        raise ValueError(
            f"No sorted story rows for {sheet_date}. "
            f"The sheet may not have rows assigned yet — "
            f"try again after rows are sorted on timeline.tbpn.com. "
            f"(API: {timeline_api_url(folder_date)})"
        )
    return timeline, sheet_date, date_iso, groups


def row_preview_payload(group: RowGroup) -> dict:
    return {
        "label": group.label,
        "tag": group.tag,
        "post_count": len(group.posts),
    }


def fetch_scrape_preview(*, folder_date: str | None = None) -> dict:
    """Fetch today's timeline and return lightweight row summaries for the picker UI."""
    timeline, sheet_date, date_iso, groups = _timeline_sheet_or_error(folder_date=folder_date)
    return {
        "date": sheet_date,
        "date_iso": date_iso,
        "post_count": timeline.count,
        "row_count": len(groups),
        "rows": [row_preview_payload(group) for group in groups],
    }


def stub_research_bundle(group: RowGroup) -> ResearchBundle:
    """Timeline-only row entry when the user skips full GPT/Tavily research."""
    return ResearchBundle(
        row=group.label,
        tag=group.tag,
        handles=group.handles,
        post_summary=build_post_summary(group),
        researched=False,
    )


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
    row_labels: list[str] | None = None,
    skip_search: bool = False,
    skip_tweets: bool = False,
    search_provider: str = "tavily",
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
    save_cache: bool = True,
) -> ScrapeDayResult:
    """Fetch today's timeline and research selected rows."""
    folder_date = default_date_pacific()
    print(f"Fetching timeline for {folder_date}...")
    timeline, sheet_date, date_iso, groups = _timeline_sheet_or_error(folder_date=folder_date)

    available = {group.label for group in groups}
    if row_labels is None:
        selected = available
    else:
        selected = set(row_labels)
        if not selected:
            raise ValueError("Select at least one row to research.")
        unknown = selected - available
        if unknown:
            raise ValueError(f"Unknown rows: {', '.join(sorted(unknown))}")

    researched_count = len(selected)
    print(
        f"Found {len(groups)} story rows ({timeline.count} total posts); "
        f"researching {researched_count}.\n"
    )

    bundles: list[ResearchBundle] = []
    for group in groups:
        if group.label in selected:
            tag_label = group.tag or "untagged"
            print(f"Researching row {group.label} ({tag_label})...")
            bundle = research_row(
                group,
                sheet_date,
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
        else:
            print(f"Skipping row {group.label} (not selected).")
            bundle = stub_research_bundle(group)
        bundles.append(bundle)

    preview_path: Path | None = None
    if save_cache:
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
    else:
        scrape = ScrapeCache(
            date=sheet_date,
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

    return ScrapeDayResult(
        date=sheet_date,
        date_iso=date_iso,
        scrape=scrape,
        preview_path=preview_path,
        researched_rows=researched_count,
    )
