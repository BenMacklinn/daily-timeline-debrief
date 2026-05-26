from __future__ import annotations

import json
import os

from openai import OpenAI
from pydantic import BaseModel, Field

from debrief.models import ArticleSnippet, RowGroup, TweetContent
from debrief.openai_utils import openai_json_schema

QUERY_SYSTEM = """You plan web search queries for a live tech/business show prep team.

The hosts don't want generic news recaps — they want material to make the topic interesting on air:
definitions, how things actually work, legislation, surprising stats, historical context, and the news.

Given tweet text, timeline metadata, and article excerpts, identify the topic and produce:

**search_queries** — 2–3 Tavily text-research queries:
1. **news** — what happened today / this week (specific entities, names, bill numbers if known)
2. **context** — definitions, "what is X", how X works, industry basics (e.g. "data center definition electricity usage")
3. **policy** (when relevant) — legislation, regulation, local opposition, zoning (skip if clearly not a policy story)

**image_queries** — 2–3 slideshow image searches (separate from text queries):
- Goal: photos a producer can show on air while hosts discuss the topic.
- Lead with the **row event or topic** from the internal tag (e.g. "Enhanced Games Las Vegas event photos").
- At most ONE query should focus on a single person; the rest should cover venue, sport, branding, crowd, etc.
- Make queries visually distinct: arena, finish line, press conference, swimming, weightlifting, etc.
- Each query must include "photo", "photos", "images", or "diagram".
- Prefer wire-service / editorial photos: AP, Reuters, Getty — add "high resolution" on at least one query.
- NEVER use generic city/venue queries ("Las Vegas photos", "swimming pool Las Vegas").
- NEVER include tangential celebrities not mentioned in the tweets.
- Avoid: SEC filings, earnings, regulation, definitions, PDFs, logos, vacation rentals, Airbnb.

Rules:
- Ignore vague internal tags like "misc" unless tweet content confirms them.
- Trust tweet content over internal tags.
- Each query: 4–14 words, no site: operators.
- Include year when timing matters.
- topic_summary: one sentence on what the hosts are building toward.
- key_entities: people, companies, bills, places mentioned.
- Do not invent facts not in the inputs."""


class QueryPlan(BaseModel):
    topic_summary: str
    search_queries: list[str] = Field(min_length=2, max_length=3)
    image_queries: list[str] = Field(min_length=2, max_length=3)
    key_entities: list[str] = Field(default_factory=list)


def _readable_date(date: str) -> str:
    parts = date.split("-")
    if len(parts) == 3:
        month, day, year = parts
        return f"{month}/{day}/{year}"
    return date


def enrich_search_queries(
    group: RowGroup,
    date: str,
    *,
    tweets: list[TweetContent],
    articles: list[ArticleSnippet],
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
) -> QueryPlan:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    schema = openai_json_schema(QueryPlan)

    tweet_lines = []
    for t in tweets:
        text = t.text or "(unavailable)"
        tweet_lines.append(f"- {t.slot} {t.handle or ''}: {text}")

    article_lines = []
    for a in articles:
        title = a.title or a.url
        preview = a.text[:500].replace("\n", " ")
        article_lines.append(f"- {title}: {preview}")

    user = f"""Date: {_readable_date(date)} ({date})
Row: {group.label}
Internal tag: {group.tag or "(none)"}
Handles: {", ".join(group.handles) if group.handles else "(none)"}
Time range: {group.time_start} → {group.time_end}
Items: {len(group.posts)}

Tweet content (oEmbed):
{chr(10).join(tweet_lines) if tweet_lines else "(no tweets)"}

Article excerpts in row:
{chr(10).join(article_lines) if article_lines else "(none)"}

Return 2–3 search queries: at least one news query AND at least one context/definition query.
Add a policy/legislation query if the topic involves regulation, local opposition, or government action.

Also return 2–3 image_queries for a live-show slideshow (hero shot, key people, product/place).
Image queries must NOT reuse the same wording as search_queries.
"""

    kwargs: dict = {
        "model": model,
        "instructions": QUERY_SYSTEM,
        "input": user,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "query_plan",
                "schema": schema,
                "strict": True,
            }
        },
    }
    if reasoning_effort and model.startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": reasoning_effort}

    response = client.responses.create(**kwargs)
    return QueryPlan.model_validate(json.loads(response.output_text))
