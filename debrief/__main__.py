from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from debrief.cache import load_scrape, resolve_serve_date, save_scrape, scrape_exists
from debrief.fetch import (
    default_date_pacific,
    fetch_timeline,
    group_posts_by_row,
    parse_date_to_iso,
    resolve_sheet_date,
    summarize_row,
)
from debrief.models import DailyDebrief, ResearchBundle, RowDebrief, RowGroup
from debrief.render import build_daily_debrief, render_html, write_outputs, write_preview
from debrief.scrape_day import scrape_rows
from debrief.server import run_server
from debrief.synthesize import synthesize_row_debrief


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a print-ready debrief from the TBPN timeline API.",
    )
    parser.add_argument(
        "--date",
        help=(
            "Optional MM-DD-YYYY for cache/output paths and historical API fetch "
            "(default: live sheet from get-posts, dated today in Pacific)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Base output directory (default: output)",
    )
    parser.add_argument(
        "--cache-dir",
        default="cache",
        help="Directory for cached timeline + research scrapes (default: cache)",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.5",
        help="OpenAI model for synthesis (default: gpt-5.5)",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="low",
        choices=["none", "low", "medium", "high", "xhigh"],
        help="Reasoning effort for GPT-5.x models (default: low)",
    )
    parser.add_argument(
        "--search",
        default="tavily",
        choices=["tavily", "duckduckgo", "openai", "none"],
        help="Web search provider (default: tavily)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and list rows only; no research or OpenAI calls",
    )
    parser.add_argument(
        "--skip-search",
        action="store_true",
        help="Skip web search; use articles and timeline metadata only",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Load cached scrape; skip API fetch, Tavily, and article extraction",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Fetch + research + save cache only; skip GPT and HTML output",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore existing cache and re-fetch/re-research before saving",
    )
    parser.add_argument(
        "--skip-tweets",
        action="store_true",
        help="Skip oEmbed tweet fetching",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Build preview.html from cache only (no GPT); shows Cache tab + Rundown if debrief exists",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Re-render debrief.html and preview.html from existing debrief.json (no GPT, no scrape)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the research UI with per-row Tavily image search (requires cached scrape)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for --serve (default: 8765)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser tab when using --serve",
    )
    return parser.parse_args(argv)


def load_or_scrape(
    args: argparse.Namespace,
    folder_date: str,
    date_iso: str,
    cache_base: Path,
) -> tuple[list[RowGroup], list[ResearchBundle], bool, str]:
    skip_search = args.skip_search or args.search == "none"
    search_provider = "duckduckgo" if args.search == "duckduckgo" else "tavily"

    if args.use_cache:
        if not scrape_exists(cache_base, date_iso):
            print(f"No cache found at {cache_base / date_iso / 'scrape.json'}", file=sys.stderr)
            return [], [], False, folder_date
        cached = load_scrape(cache_base, date_iso)
        print(
            f"Loaded cache from {cached.scraped_at:%Y-%m-%d %H:%M} "
            f"({len(cached.rows)} rows, {cached.post_count} posts)"
        )
        groups = [row.group for row in cached.rows]
        bundles = [row.research for row in cached.rows]
        return groups, bundles, True, cached.date

    if args.date and args.date != default_date_pacific():
        print(f"Fetching timeline for {args.date}...")
    else:
        print("Fetching live timeline sheet...")
    timeline = fetch_timeline(date=args.date)
    sheet_date = resolve_sheet_date(timeline, fallback=folder_date)
    date_iso = parse_date_to_iso(sheet_date)
    groups = group_posts_by_row(timeline.posts)
    if not groups:
        return [], [], False, sheet_date

    print(f"Found {len(groups)} story rows ({timeline.count} total posts).\n")
    bundles = scrape_rows(
        groups,
        sheet_date,
        skip_search=skip_search,
        skip_tweets=args.skip_tweets,
        search_provider=search_provider,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
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

    return groups, bundles, True, sheet_date


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    folder_date = args.date or default_date_pacific()
    date_iso = parse_date_to_iso(folder_date)
    cache_base = Path(args.cache_dir)
    out_dir = Path(args.output_dir) / date_iso

    if args.serve:
        serve_date, date_iso = resolve_serve_date(cache_base, folder_date=args.date)
        out_dir = Path(args.output_dir) / date_iso
        if not args.date:
            print(f"Serving latest cache: {date_iso} ({serve_date})")
        try:
            run_server(
                date_iso=date_iso,
                output_dir=out_dir,
                cache_dir=cache_base,
                output_base=Path(args.output_dir),
                skip_search=args.skip_search or args.search == "none",
                skip_tweets=args.skip_tweets,
                search_provider="duckduckgo" if args.search == "duckduckgo" else "tavily",
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                search_fallback=args.search == "openai",
                port=args.port,
                open_browser=not args.no_browser,
            )
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            print(
                f"Scrape first: python -m debrief --date {folder_date} --scrape-only",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.use_cache and args.refresh_cache:
        print("Cannot use --use-cache and --refresh-cache together.", file=sys.stderr)
        return 1

    if args.preview:
        if not scrape_exists(cache_base, date_iso):
            print(f"No cache found at {cache_base / date_iso / 'scrape.json'}", file=sys.stderr)
            return 1
        cached = load_scrape(cache_base, date_iso)
        has_debrief = (out_dir / "debrief.html").exists()
        preview_path = write_preview(cached, out_dir, has_debrief=has_debrief)
        print(f"Preview → {preview_path.resolve()}")
        return 0

    if args.render_only:
        json_path = out_dir / "debrief.json"
        if not json_path.exists():
            print(f"No debrief found at {json_path}", file=sys.stderr)
            return 1
        import json as json_mod

        daily = DailyDebrief.model_validate(json_mod.loads(json_path.read_text(encoding="utf-8")))
        html_path = out_dir / "debrief.html"
        html_path.write_text(render_html(daily), encoding="utf-8")
        preview_path: Path | None = None
        if scrape_exists(cache_base, date_iso):
            cached = load_scrape(cache_base, date_iso)
            preview_path = write_preview(cached, out_dir, has_debrief=True)
        print(f"HTML: {html_path.resolve()}")
        if preview_path:
            print(f"Preview: {preview_path.resolve()}")
        return 0

    if args.dry_run:
        if args.use_cache and scrape_exists(cache_base, date_iso):
            cached = load_scrape(cache_base, date_iso)
            groups = [row.group for row in cached.rows]
            print(f"Loaded cache ({len(groups)} rows)\n")
        else:
            if args.date and args.date != default_date_pacific():
                print(f"Fetching timeline for {args.date}...")
            else:
                print("Fetching live timeline sheet...")
            try:
                timeline = fetch_timeline(date=args.date)
                folder_date = resolve_sheet_date(
                    timeline, fallback=args.date or default_date_pacific()
                )
            except Exception as exc:
                print(f"Error fetching timeline: {exc}", file=sys.stderr)
                return 1
            groups = group_posts_by_row(timeline.posts)
            if not groups:
                print("No sorted story rows found for this date.", file=sys.stderr)
                return 1
            print(f"Found {len(groups)} story rows ({timeline.count} total posts).\n")

        for group in groups:
            print(summarize_row(group))
        return 0

    sheet_date = folder_date

    try:
        if args.use_cache:
            groups, bundles, ok, sheet_date = load_or_scrape(
                args, folder_date, date_iso, cache_base
            )
        elif args.refresh_cache or not scrape_exists(cache_base, date_iso):
            groups, bundles, ok, sheet_date = load_or_scrape(
                args, folder_date, date_iso, cache_base
            )
        else:
            print(f"Using existing cache for {date_iso} (pass --refresh-cache to re-scrape)")
            cached = load_scrape(cache_base, date_iso)
            sheet_date = cached.date
            groups = [row.group for row in cached.rows]
            bundles = [row.research for row in cached.rows]
            ok = True
    except Exception as exc:
        print(f"Error loading/scraping data: {exc}", file=sys.stderr)
        return 1

    date_iso = parse_date_to_iso(sheet_date)
    out_dir = Path(args.output_dir) / date_iso

    if not ok or not groups:
        print("No sorted story rows found for this date.", file=sys.stderr)
        return 1

    if args.scrape_only:
        cached = load_scrape(cache_base, date_iso)
        preview_path = write_preview(cached, out_dir, has_debrief=False)
        print("Scrape complete (--scrape-only).")
        print(f"  Preview: {preview_path.resolve()}")
        print(f"  Research UI: python -m debrief --date {sheet_date} --serve")
        return 0

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is required. Set it in .env", file=sys.stderr)
        return 1

    search_fallback = args.search == "openai"
    row_debriefs: list[RowDebrief] = []

    for group, bundle in zip(groups, bundles, strict=True):
        print(f"Writing debrief for row {group.label}...")
        try:
            debrief = synthesize_row_debrief(
                group,
                bundle,
                sheet_date,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                search_fallback=search_fallback,
            )
        except Exception as exc:
            print(f"  Error synthesizing row {group.label}: {exc}", file=sys.stderr)
            return 1

        row_debriefs.append(debrief)
        print(f"  ✓ {debrief.headline}\n")

    daily = build_daily_debrief(
        date=sheet_date,
        date_iso=date_iso,
        rows=row_debriefs,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )

    cached = load_scrape(cache_base, date_iso)
    html_path, json_path, preview_path = write_outputs(daily, out_dir, scrape=cached)

    print("Done.")
    print(f"  Preview: {preview_path.resolve() if preview_path else 'n/a'}")
    print(f"  HTML: {html_path.resolve()}")
    print(f"  JSON: {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
