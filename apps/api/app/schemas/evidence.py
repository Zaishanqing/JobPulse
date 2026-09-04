from datetime import date

from pydantic import BaseModel, Field


class EvidenceSourceCreate(BaseModel):
    source_type: str = Field(min_length=1, max_length=64)
    source_name: str | None = None
    title: str = Field(min_length=1, max_length=255)
    url: str | None = None
    raw_text: str | None = None
    publish_date: date | None = None
    credibility_score: float = Field(default=0.8, ge=0.0, le=1.0)
    related_object_type: str | None = None
    related_object_id: str | None = None
    source_platform: str | None = Field(default=None, max_length=128)
    enterprise_id: str | None = Field(default=None, max_length=128)
    template_cluster_id: str | None = Field(default=None, max_length=128)
    source_version: str | None = Field(default=None, max_length=128)
    source_fact_id: str | None = Field(default=None, max_length=128)
    source_jd_id: str | None = Field(default=None, max_length=128)
    source_jd_version_id: str | None = Field(default=None, max_length=36)


class EvidenceSourceUpdate(BaseModel):
    source_type: str | None = Field(default=None, min_length=1, max_length=64)
    source_name: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = None
    raw_text: str | None = None
    publish_date: date | None = None
    credibility_score: float | None = Field(default=None, ge=0.0, le=1.0)
    related_object_type: str | None = None
    related_object_id: str | None = None
    source_platform: str | None = Field(default=None, max_length=128)
    enterprise_id: str | None = Field(default=None, max_length=128)
    template_cluster_id: str | None = Field(default=None, max_length=128)
    source_version: str | None = Field(default=None, max_length=128)
    source_fact_id: str | None = Field(default=None, max_length=128)
    source_jd_id: str | None = Field(default=None, max_length=128)
    source_jd_version_id: str | None = Field(default=None, max_length=36)
