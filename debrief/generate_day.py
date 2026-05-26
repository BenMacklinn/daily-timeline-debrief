from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from debrief.cache import load_scrape, scrape_exists
from debrief.render import build_daily_debrief, write_outputs
from debrief.synthesize import synthesize_row_debrief


@dataclass
class GenerateDebriefResult:
    date: str
    date_iso: str
    html_path: Path
    json_path: Path
    preview_path: Path | None
    row_count: int


def generate_debrief_from_cache(
    *,
    cache_base: Path,
    output_base: Path,
    date_iso: str,
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
    search_fallback: bool = False,
) -> GenerateDebriefResult:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required. Set it in .env")

    if not scrape_exists(cache_base, date_iso):
        raise FileNotFoundError(f"No cached scrape for {date_iso}")

    scrape = load_scrape(cache_base, date_iso)
    out_dir = output_base / date_iso
    row_debriefs = []

    for row in scrape.rows:
        if not row.research.researched:
            print(f"Skipping row {row.group.label} (not researched in scrape).")
            continue
        print(f"Writing debrief for row {row.group.label}...")
        debrief = synthesize_row_debrief(
            row.group,
            row.research,
            scrape.date,
            model=model,
            reasoning_effort=reasoning_effort,
            search_fallback=search_fallback,
        )
        row_debriefs.append(debrief)
        print(f"  ✓ {debrief.headline}")

    if not row_debriefs:
        raise RuntimeError("No researched rows in cache. Run a full scrape on at least one row first.")

    daily = build_daily_debrief(
        date=scrape.date,
        date_iso=date_iso,
        rows=row_debriefs,
        model=model,
        reasoning_effort=reasoning_effort,
    )

    html_path, json_path, preview_path = write_outputs(daily, out_dir, scrape=scrape)
    print(f"Debrief → {html_path.resolve()}")

    return GenerateDebriefResult(
        date=scrape.date,
        date_iso=date_iso,
        html_path=html_path,
        json_path=json_path,
        preview_path=preview_path,
        row_count=len(row_debriefs),
    )
