from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class Position(BaseModel):
    type: str
    label: str | None = None
    position: int
    display: str


class Post(BaseModel):
    id: int
    links: list[str] = Field(default_factory=list)
    tweet_link: str | None = None
    article_link: str | None = None
    image_url: str | None = None
    sender: str
    received_at: str
    received_at_pacific: str
    sent_count: int = 1
    table_index: int
    position: Position
    label: str | None = None
    tag: str | None = None
    type: str
    archive_group: str | None = None


class TimelineResponse(BaseModel):
    date: str | None = None
    count: int
    posts: list[Post]


class RowGroup(BaseModel):
    label: str
    tag: str | None
    posts: list[Post]
    handles: list[str] = Field(default_factory=list)
    article_urls: list[str] = Field(default_factory=list)
    time_start: str | None = None
    time_end: str | None = None


class ArticleSnippet(BaseModel):
    url: str
    title: str | None = None
    text: str


class SearchSnippet(BaseModel):
    query: str
    title: str
    url: str
    content: str


class ImageResult(BaseModel):
    url: str
    description: str | None = None
    source_url: str | None = None


class TweetContent(BaseModel):
    slot: str
    handle: str | None = None
    tweet_link: str | None = None
    image_url: str = ""
    text: str | None = None


class ResearchBundle(BaseModel):
    row: str
    tag: str | None
    handles: list[str] = Field(default_factory=list)
    tweets: list[TweetContent] = Field(default_factory=list)
    topic_summary: str | None = None
    search_queries_used: list[str] = Field(default_factory=list)
    image_queries_used: list[str] = Field(default_factory=list)
    key_entities: list[str] = Field(default_factory=list)
    articles: list[ArticleSnippet] = Field(default_factory=list)
    search_results: list[SearchSnippet] = Field(default_factory=list)
    images: list[ImageResult] = Field(default_factory=list)
    post_summary: str = ""


class RowScrape(BaseModel):
    group: RowGroup
    research: ResearchBundle


class ScrapeCache(BaseModel):
    version: int = 3
    date: str
    date_iso: str
    scraped_at: datetime
    post_count: int
    search_provider: str
    skip_search: bool
    timeline: TimelineResponse
    rows: list[RowScrape]


class RowDebrief(BaseModel):
    row: str
    tag: str | None
    headline: str
    key_news: list[str] = Field(min_length=2, max_length=4)
    background: list[str] = Field(min_length=1, max_length=3)
    hard_facts: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("hard_facts")
    @classmethod
    def hard_facts_word_limit(cls, bullets: list[str]) -> list[str]:
        for bullet in bullets:
            words = bullet.split()
            if len(words) > 20:
                raise ValueError(
                    f"hard_facts bullet exceeds 20 words ({len(words)}): {bullet!r}"
                )
        return bullets


class DailyDebrief(BaseModel):
    date: str
    date_iso: str
    generated_at: datetime
    rows: list[RowDebrief]
    model: str
    reasoning_effort: str
