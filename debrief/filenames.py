from __future__ import annotations

from datetime import datetime


def fast_facts_pdf_filename(date_iso: str) -> str:
    parsed = datetime.strptime(date_iso, "%Y-%m-%d")
    return f"{parsed.strftime('%B')}_{parsed.day}_Fast_Facts.pdf"
