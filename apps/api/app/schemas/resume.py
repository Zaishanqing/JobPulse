from typing import Any

from pydantic import BaseModel, Field, field_validator


class ResumeTextCreate(BaseModel):
    raw_text: str = Field(min_length=1)


class ResumeUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name cannot be blank")
        return normalized


class ResumeResponse(BaseModel):
    resume_id: str
    user_id: str
    source_type: str
    file_id: str | None = None
    raw_text: str
    parse_status: str


class ResumeParseResultResponse(BaseModel):
    resume_id: str
    education: list[dict[str, Any]]
    projects: list[dict[str, Any]]
    internships: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    certificates: list[dict[str, Any]]
    competitions: list[dict[str, Any]]
    parse_confidence: float
    need_review: bool


class ResumeParseResultUpdate(BaseModel):
    education: list[dict[str, Any]] | None = None
    projects: list[dict[str, Any]] | None = None
    internships: list[dict[str, Any]] | None = None
    skills: list[dict[str, Any]] | None = None
    certificates: list[dict[str, Any]] | None = None
    competitions: list[dict[str, Any]] | None = None
    parse_confidence: float | None = Field(default=None, ge=0, le=1)
    need_review: bool | None = None
