from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from debrief.fetch import default_date_pacific, parse_date_to_iso
from debrief.filenames import fast_facts_pdf_filename
from debrief.image_urls import display_image_url
from debrief.models import DailyDebrief, ScrapeCache
from debrief.pdf import write_pdf

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
GRAPHICS_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "graphics"
GRAPHICS_OUTPUT_DIRNAME = "graphics-assets"

RUNDOWN_FACT_MAX_CHARS = 62
RUNDOWN_MAX_GRAPHIC_FACTS = 4
RUNDOWN_MAX_EDITABLE_FACTS = 6

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def markdown_bold(text: str) -> Markup:
    """Escape HTML, then render **keyword** markers as <strong>."""
    escaped = str(escape(text))
    return Markup(_BOLD_RE.sub(r"<strong>\1</strong>", escaped))


def graphic_text(text: str, max_chars: int) -> str:
    """Prepare rundown copy using the same hard limits as the Flowics graphics app."""
    cleaned = _BOLD_RE.sub(r"\1", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned

    cut = cleaned[:max_chars]
    last_space = cut.rfind(" ")
    if last_space > max_chars - 18:
        return cut[:last_space].rstrip()
    return cut.rstrip()


def get_template_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["markdown_bold"] = markdown_bold
    env.filters["graphic_text"] = graphic_text
    env.filters["display_image_url"] = display_image_url
    env.globals["fast_facts_pdf_filename"] = fast_facts_pdf_filename
    return env


def render_html(debrief: DailyDebrief) -> str:
    env = get_template_env()
    template = env.get_template("debrief.html.j2")
    return template.render(debrief=debrief)


def render_preview(
    scrape: ScrapeCache,
    *,
    has_debrief: bool,
    today_date_iso: str | None = None,
) -> str:
    env = get_template_env()
    template = env.get_template("preview.html.j2")
    if today_date_iso is None:
        today_date_iso = parse_date_to_iso(default_date_pacific())
    return template.render(
        scrape=scrape,
        has_debrief=has_debrief,
        today_date_iso=today_date_iso,
    )


def render_empty_preview(
    *,
    date_iso: str,
    today_date_iso: str | None = None,
) -> str:
    env = get_template_env()
    template = env.get_template("empty_preview.html.j2")
    if today_date_iso is None:
        today_date_iso = parse_date_to_iso(default_date_pacific())
    return template.render(date_iso=date_iso, today_date_iso=today_date_iso)


def render_graphics(debrief: DailyDebrief) -> str:
    env = get_template_env()
    template = env.get_template("graphics.html.j2")
    return template.render(
        debrief=debrief,
        fact_max_chars=RUNDOWN_FACT_MAX_CHARS,
        max_facts=RUNDOWN_MAX_GRAPHIC_FACTS,
        editable_max_facts=RUNDOWN_MAX_EDITABLE_FACTS,
    )


def write_graphics(debrief: DailyDebrief, output_dir: Path) -> Path:
    """Write the Flowics-style graphics workspace and its local render assets."""
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_output = output_dir / GRAPHICS_OUTPUT_DIRNAME
    asset_output.mkdir(parents=True, exist_ok=True)

    for source in GRAPHICS_ASSET_DIR.iterdir():
        if source.is_file():
            shutil.copy2(source, asset_output / source.name)

    graphics_path = output_dir / "graphics.html"
    graphics_path.write_text(render_graphics(debrief), encoding="utf-8")
    return graphics_path


def write_outputs(
    debrief: DailyDebrief,
    output_dir: Path,
    *,
    scrape: ScrapeCache | None = None,
) -> tuple[Path, Path, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "debrief.html"
    json_path = output_dir / "debrief.json"
    pdf_path = output_dir / "debrief.pdf"

    html_path.write_text(render_html(debrief), encoding="utf-8")
    json_path.write_text(
        json.dumps(debrief.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_pdf(debrief, pdf_path)
    write_graphics(debrief, output_dir)

    return html_path, json_path, None


def write_preview(scrape: ScrapeCache, output_dir: Path, *, has_debrief: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = output_dir / "preview.html"
    preview_path.write_text(render_preview(scrape, has_debrief=has_debrief), encoding="utf-8")
    return preview_path


def build_daily_debrief(
    *,
    date: str,
    date_iso: str,
    rows: list,
    model: str,
    reasoning_effort: str,
) -> DailyDebrief:
    return DailyDebrief(
        date=date,
        date_iso=date_iso,
        generated_at=datetime.now(),
        rows=rows,
        model=model,
        reasoning_effort=reasoning_effort,
    )
