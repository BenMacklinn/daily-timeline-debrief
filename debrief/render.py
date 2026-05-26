from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from debrief.fetch import default_date_pacific, parse_date_to_iso
from debrief.image_urls import display_image_url
from debrief.models import DailyDebrief, ScrapeCache

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def markdown_bold(text: str) -> Markup:
    """Escape HTML, then render **keyword** markers as <strong>."""
    escaped = str(escape(text))
    return Markup(_BOLD_RE.sub(r"<strong>\1</strong>", escaped))


def get_template_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["markdown_bold"] = markdown_bold
    env.filters["display_image_url"] = display_image_url
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


def write_outputs(
    debrief: DailyDebrief,
    output_dir: Path,
    *,
    scrape: ScrapeCache | None = None,
) -> tuple[Path, Path, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "debrief.html"
    json_path = output_dir / "debrief.json"

    html_path.write_text(render_html(debrief), encoding="utf-8")
    json_path.write_text(
        json.dumps(debrief.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    written_preview: Path | None = None
    if scrape is not None:
        written_preview = write_preview(scrape, output_dir, has_debrief=True)

    return html_path, json_path, written_preview


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
