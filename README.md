# Daily Timeline Debrief

Pull the live TBPN timeline sheet, research each story, and generate a print-ready HTML debrief.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# Add OPENAI_API_KEY and TAVILY_API_KEY to .env
```

Requires **Python 3.11+**.

## Usage

```bash
# Today's sheet (Pacific date in query string)
python -m debrief

# Historical sheet + cache/output for a specific day
python -m debrief --date 05-18-2026

# Preview rows without API calls to OpenAI/Tavily
python -m debrief --dry-run
```

## Caching (iterate on GPT/HTML without re-scraping)

The first run for a date saves timeline + research to `cache/YYYY-MM-DD/scrape.json`.

```bash
# Scrape once (API + Tavily + articles) — no GPT cost
python -m debrief --date 05-18-2026 --scrape-only

# Re-run GPT + HTML from cache only (fast, no Tavily)
python -m debrief --date 05-18-2026 --use-cache

# Full run: uses cache if present, otherwise scrapes and saves cache
python -m debrief --date 05-18-2026

# Force a fresh scrape
python -m debrief --date 05-18-2026 --refresh-cache --scrape-only

# Rundown UI: choose timeline sections, research them, and generate the rundown
python -m debrief --date 05-18-2026 --serve
```

Research pipeline per row:
1. **oEmbed** — fetch tweet text from `publish.twitter.com/oembed` (free, no vision)
2. **GPT** — infer topic + craft 1–2 Tavily queries from tweet text
3. **Tavily** — search with enriched queries
4. **GPT** — write debrief (use `--use-cache` to skip steps 1–3)

Output is written to `output/YYYY-MM-DD/debrief.html`, `debrief.pdf`, and `debrief.json`.

Open the HTML in a browser, or use **Download PDF** in the rundown UI.

## Deploy (Vercel)

The rundown UI and API routes deploy as a Python serverless function (`api/index.py`).

```bash
# One-time: link project and set secrets in the Vercel dashboard (or CLI)
vercel link
vercel env add OPENAI_API_KEY
vercel env add TAVILY_API_KEY

# Deploy
vercel deploy --prod
```

On Vercel, cache/output live under `/tmp` for the lifetime of a function instance. Use **Choose sections** on first visit (or after a cold start). Hobby plans cap function duration at 10s — scraping/generation may require Pro (this repo sets `maxDuration: 300`).

Optional env vars: `DEBRIEF_DATE` (MM-DD-YYYY), `OPENAI_MODEL`, `REASONING_EFFORT`, `SEARCH_PROVIDER`.
