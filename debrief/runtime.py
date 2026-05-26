from __future__ import annotations

import os

from dotenv import load_dotenv

from debrief.cache import resolve_serve_date, scrape_exists
from debrief.paths import cache_base, ensure_storage_dirs, output_base
from debrief.render import write_preview
from debrief.server import DebriefServer


def load_env() -> None:
    load_dotenv()


def build_debrief_server(*, folder_date: str | None = None) -> DebriefServer:
    """Construct the app for local serve or Vercel serverless."""
    load_env()
    ensure_storage_dirs()

    cache_dir = cache_base()
    out_base = output_base()
    serve_date, date_iso = resolve_serve_date(cache_dir, folder_date=folder_date or os.getenv("DEBRIEF_DATE"))

    output_dir = out_base / date_iso
    output_dir.mkdir(parents=True, exist_ok=True)

    if scrape_exists(cache_dir, date_iso):
        preview_path = output_dir / "preview.html"
        if not preview_path.exists():
            from debrief.cache import load_scrape

            scrape = load_scrape(cache_dir, date_iso)
            has_debrief = (output_dir / "debrief.html").exists()
            write_preview(scrape, output_dir, has_debrief=has_debrief)

    return DebriefServer(
        date_iso=date_iso,
        output_dir=output_dir,
        cache_dir=cache_dir,
        output_base=out_base,
        skip_search=os.getenv("SKIP_SEARCH", "").lower() in {"1", "true", "yes"},
        skip_tweets=os.getenv("SKIP_TWEETS", "").lower() in {"1", "true", "yes"},
        search_provider=os.getenv("SEARCH_PROVIDER", "tavily"),
        model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        reasoning_effort=os.getenv("REASONING_EFFORT", "low"),
        search_fallback=os.getenv("SEARCH_FALLBACK", "").lower() in {"1", "true", "yes"},
    )
