from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=32)
    endpoint_config: dict[str, Any] = Field(default_factory=dict)
    auth_config: dict[str, Any] = Field(default_factory=dict)
    rate_limit_rps: float = Field(default=1.0, ge=0.1, le=100.0)
    compliance_policy: dict[str, Any] = Field(default_factory=dict)


class UpdateSourceRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    source_type: str | None = Field(default=None, min_length=1, max_length=32)
    endpoint_config: dict[str, Any] | None = None
    auth_config: dict[str, Any] | None = None
    rate_limit_rps: float | None = Field(default=None, ge=0.1, le=100.0)
    compliance_policy: dict[str, Any] | None = None


class CreateCrawlJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=36)
    window_start: datetime
    window_end: datetime
    max_retries: int = Field(default=3, ge=0, le=10)
    rate_limit_rps: float | None = Field(default=None, ge=0.1, le=100.0)

    @model_validator(mode="after")
    def validate_window(self):
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ValueError("timestamps must include timezone")
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end")
        return self


class CreateBundleRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=36)
    snapshot_ids: list[str] = Field(min_length=1, max_length=10000)
    bundle_type: str = Field(min_length=1, max_length=32)
