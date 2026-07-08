from __future__ import annotations

import os

from dotenv import load_dotenv

from debrief.fetch import default_date_pacific, parse_date_to_iso
from debrief.paths import cache_base, ensure_storage_dirs, output_base
from debrief.server import DebriefServer


def load_env() -> None:
    load_dotenv()


def build_debrief_server(*, folder_date: str | None = None) -> DebriefServer:
    """Construct the app for local serve or Vercel serverless."""
    load_env()
    ensure_storage_dirs()

    cache_dir = cache_base()
    out_base = output_base()
    serve_date = folder_date or os.getenv("DEBRIEF_DATE") or default_date_pacific()
    date_iso = parse_date_to_iso(serve_date)

    output_dir = out_base / date_iso
    output_dir.mkdir(parents=True, exist_ok=True)

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
