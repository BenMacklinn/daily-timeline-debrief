from __future__ import annotations

import json
import os
import re

from openai import OpenAI
from pydantic import BaseModel, Field, field_validator

from debrief.models import ResearchBundle, RowDebrief, RowGroup
from debrief.openai_utils import openai_json_schema


ALTERNATIVE_FACT_MAX_CHARS = 62


class AlternativeFact(BaseModel):
    fact: str = Field(min_length=1, max_length=ALTERNATIVE_FACT_MAX_CHARS)

    @field_validator("fact")
    @classmethod
    def clean_fact(cls, fact: str) -> str:
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", fact).strip()
        if not cleaned:
            raise ValueError("fact cannot be empty")
        if len(cleaned.split()) > 20:
            raise ValueError("fact cannot exceed 20 words")
        return cleaned

SYSTEM_PROMPT = """You write TBPN-style live reaction notes for tech/business news stories.

These are NOT generic summaries or company profiles. Hosts are reacting live to headlines, tweets, launches, earnings, policy moves, funding rounds, viral moments, and market events.

Focus on:

* What happened and why it matters
* Important numbers, legislation, market impact, or technical details
* Non-obvious or contrarian angles
* Definitions/context hosts may need live
* Interesting implications across AI, markets, chips, energy, defense, startups, and geopolitics

Output format (JSON field names):

headline
* One short sentence
* Punchy, specific, discussion-oriented
* Maximum 60 characters, including spaces and punctuation

key_news
* 2–4 concise bullets explaining what happened
* Include important numbers, companies, products, policy details, or reactions

background
* 1–3 concise bullets giving context needed to understand the story
* Include definitions or industry structure when useful

hard_facts
* **Max 6 bullets** — pick the best facts from research; merge related ones to fit more in
* Standalone facts: keep to **~10 words**
* **Merged facts:** combine related stats/dates/places into one bullet — up to **~20 words** (e.g. "Trial opened April 28; Musk testified April 29, Altman May 12")
* Maximum 78 characters per bullet, including spaces and punctuation
* Include stats, dates, places, names, outcomes — mine research for interesting nuggets hosts might not know
* **No redundancy** with key_news or background
* No analysis — bare facts only; do not use **bold** in hard_facts
* Good: "Unanimous verdict after 90 minutes deliberation"
* Good: "WHO: eight cases, three deaths; 38% case fatality ratio"
* Good: "Gallup 71% oppose local data centers; demand 80GW to 150GW by 2028"

Rules:

* Use only facts from provided research/tweets
* No invented quotes or unsupported analysis
* Prefer concrete details over vague summaries
* Avoid PR/corporate language
* Every bullet should contain something worth saying live on-air
* Bold important keywords by wrapping them in **double asterisks** in headline, key_news, and background only — company names, people, products, dollar amounts, percentages, bill names, dates, and technical terms (e.g. **OpenAI**, **$134 billion**, **13F**). Do not bold entire sentences; bold 1–4 high-signal terms per bullet.

Tone:

* Concise, smart, high-signal
* Written for fast-moving live discussion
* Should resemble internal TBPN rundown notes"""


def _build_user_prompt(group: RowGroup, bundle: ResearchBundle, date: str) -> str:
    parts = [
        f"Date: {date}",
        f"Row: {group.label}",
        f"Tag: {group.tag or '(none)'}",
        f"Topic: {bundle.topic_summary or '(none)'}",
        f"Time range: {group.time_start} → {group.time_end}",
        "",
        "Timeline:",
        bundle.post_summary,
    ]

    if bundle.tweets:
        parts.append("\nTweets:")
        for tweet in bundle.tweets:
            if tweet.text:
                parts.append(f"- {tweet.slot} {tweet.handle or ''}: {tweet.text}")

    if bundle.articles:
        parts.append("\nArticles:")
        for article in bundle.articles:
            title = article.title or article.url
            parts.append(f"\n[{title}]({article.url})\n{article.text}")

    if bundle.search_results:
        parts.append("\nResearch snippets:")
        for hit in bundle.search_results:
            parts.append(
                f"\nQuery: {hit.query}\nTitle: {hit.title}\nURL: {hit.url}\n{hit.content}"
            )

    return "\n".join(parts)


def synthesize_row_debrief(
    group: RowGroup,
    bundle: ResearchBundle,
    date: str,
    *,
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
    search_fallback: bool = False,
) -> RowDebrief:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    schema = openai_json_schema(RowDebrief)

    kwargs: dict = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "input": _build_user_prompt(group, bundle, date),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "row_debrief",
                "schema": schema,
                "strict": True,
            }
        },
    }

    if reasoning_effort and model.startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": reasoning_effort}

    if search_fallback:
        kwargs["tools"] = [{"type": "web_search_preview"}]

    response = client.responses.create(**kwargs)

    raw = response.output_text
    data = json.loads(raw)
    data["row"] = group.label
    data["tag"] = group.tag
    return RowDebrief.model_validate(data)


def _fact_key(fact: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", fact.casefold()).strip()


def synthesize_alternative_fact(
    row: RowDebrief,
    existing_facts: list[str],
    *,
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
) -> str:
    """Generate one fact grounded in a row's rundown and distinct from current slots."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required to generate a replacement fact.")
    client = OpenAI(api_key=api_key)
    exclusions = [fact.strip() for fact in existing_facts if fact.strip()]
    excluded_keys = {_fact_key(fact) for fact in exclusions}
    schema = openai_json_schema(AlternativeFact)
    rundown = {
        "headline": row.headline,
        "key_news": row.key_news,
        "background": row.background,
        "original_hard_facts": row.hard_facts,
    }
    prompt = {
        "task": (
            "Write one new TBPN fast fact supported by the generated rundown. "
            "It must add a concrete detail that is not the same as, or a paraphrase of, "
            "any fact currently in the six slots. Return a bare fact with no analysis, "
            "markdown, label, or lead-in. Maximum 62 characters and 20 words."
        ),
        "row": row.row,
        "generated_rundown": rundown,
        "facts_currently_in_slots": exclusions,
    }

    kwargs: dict = {
        "model": model,
        "instructions": (
            "You replace one fast fact in a live tech and business rundown. Use only "
            "information explicitly present in the supplied generated rundown. Never "
            "invent details. Avoid semantic duplication with every excluded fact."
        ),
        "input": json.dumps(prompt, ensure_ascii=False),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "alternative_fact",
                "schema": schema,
                "strict": True,
            }
        },
    }
    if reasoning_effort and model.startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": reasoning_effort}

    response = client.responses.create(**kwargs)
    candidate = AlternativeFact.model_validate(json.loads(response.output_text)).fact
    if _fact_key(candidate) in excluded_keys:
        raise RuntimeError("The model returned a fact already in the current slots. Try again.")
    return candidate
