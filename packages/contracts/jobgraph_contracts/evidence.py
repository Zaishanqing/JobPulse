"""Shared Evidence span contract used by JD/CV Extraction and Matching."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from jobgraph_contracts.base import StrictContract


EvidenceAlignment = Literal["exact", "normalized_exact", "unresolved"]


class Evidence(StrictContract):
    model_config = ConfigDict(validate_assignment=True)

    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    alignment: EvidenceAlignment = "unresolved"
    occurrence_index: int | None = Field(default=None, ge=0)

    @field_validator("source_id", mode="before")
    @classmethod
    def _strip_source_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("quote", mode="before")
    @classmethod
    def _quote_must_be_non_empty(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("quote must be a string")
        if not value.strip():
            raise ValueError("quote must not be empty")
        return value

    def is_exact_for(self, raw_text: str) -> bool:
        return (
            self.alignment == "exact"
            and self.start is not None
            and self.end is not None
            and raw_text[self.start : self.end] == self.quote
        )

    @model_validator(mode="after")
    def validate_span(self) -> "Evidence":
        if (self.start is None) != (self.end is None):
            raise ValueError("evidence start and end must be supplied together")
        if self.start is not None and self.end is not None:
            if self.end < self.start:
                raise ValueError("evidence end must not precede start")
            if self.end == self.start:
                raise ValueError("evidence end must be greater than start")
        if self.alignment == "exact" and (self.start is None or self.end is None):
            raise ValueError("exact evidence requires start and end")
        return self
