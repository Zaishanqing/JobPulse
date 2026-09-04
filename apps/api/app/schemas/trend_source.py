from datetime import date

from pydantic import BaseModel, Field


class TrendSourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    source_name: str | None = None
    url: str | None = None
    raw_text: str = Field(min_length=1)
    publish_date: date | None = None
    credibility_score: float = Field(default=0.8, ge=0.0, le=1.0)


class TrendSourceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    source_name: str | None = None
    url: str | None = None
    raw_text: str | None = Field(default=None, min_length=1)
    publish_date: date | None = None
    credibility_score: float | None = Field(default=None, ge=0.0, le=1.0)
    parsed_keywords: list[str] | None = None
