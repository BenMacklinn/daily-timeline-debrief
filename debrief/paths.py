from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cache_base() -> Path:
    override = os.getenv("CACHE_DIR")
    if override:
        return Path(override)
    if os.getenv("VERCEL"):
        return Path("/tmp/daily-timeline-debrief/cache")
    return ROOT / "cache"


def output_base() -> Path:
    override = os.getenv("OUTPUT_DIR")
    if override:
        return Path(override)
    if os.getenv("VERCEL"):
        return Path("/tmp/daily-timeline-debrief/output")
    return ROOT / "output"


def ensure_storage_dirs() -> None:
    cache_base().mkdir(parents=True, exist_ok=True)
    output_base().mkdir(parents=True, exist_ok=True)
