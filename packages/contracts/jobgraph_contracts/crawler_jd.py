"""Crawler → downstream JD envelope (V1).

Every Crawler production path MUST wrap its output in :class:`CrawlerJDEnvelopeV1`.
Semantic fields (responsibilities, requirements, skills, salary, education,
experience) MUST NOT appear in this contract — those belong to Extraction.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import field_validator, model_validator

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.source_identity import (
    compute_content_hash,
    ensure_timezone_aware,
    normalize_source_record_id,
    validate_source_platform,
)

# ---------------------------------------------------------------------------
# Re-usable validators
# ---------------------------------------------------------------------------


def _non_empty_stripped(v: str, label: str) -> str:
    """Validate-and-normalise: strip whitespace, reject empty."""
    if not isinstance(v, str):
        raise TypeError(f"{label} must be a string")
    stripped = v.strip()
    if not stripped:
        raise ValueError(f"{label} must be non-empty after stripping whitespace")
    return stripped


def _non_empty_preserved(v: str, label: str) -> str:
    """Validate-only: check contains non-whitespace, return original unchanged."""
    if not isinstance(v, str):
        raise TypeError(f"{label} must be a string")
    if not v.strip():
        raise ValueError(f"{label} must contain non-whitespace content")
    return v


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class CrawlerJDEnvelopeV1(StrictContract):
    """Stable envelope that carries raw crawled JD data to downstream services.

    The envelope is the **only** contract between Crawler and the rest of the
    system.  Every field documents the origin of the data (``*_raw``) so that
    consumers never mistake a website-returned value for an authoritative
    system fact.
    """

    schema_version: Literal["crawler-jd-v1"] = "crawler-jd-v1"

    # --- source identity ---------------------------------------------------
    source_record_id: str
    source_platform: str
    source_url: str | None = None

    # --- raw signals from the source website -------------------------------
    job_title_raw: str | None = None
    company_name_raw: str | None = None
    region_raw: str | None = None
    publish_time_raw: str | None = None

    # --- crawl metadata ----------------------------------------------------
    crawl_time: datetime

    # --- stable text -------------------------------------------------------
    raw_text: str
    raw_payload: dict[str, Any]
    raw_html: str | None = None
    content_hash: str | None = None

    # --- explicit source version -------------------------------------------
    text_canonicalization_version: str
    source_version: str = "1"

    # == validators =========================================================

    @field_validator("source_record_id", mode="before")
    @classmethod
    def _validate_source_record_id(cls, v: str) -> str:
        if not isinstance(v, str):
            raise TypeError(f"source_record_id must be a string, got {type(v)}")
        return normalize_source_record_id(v)

    @field_validator("source_platform", mode="before")
    @classmethod
    def _validate_source_platform(cls, v: str) -> str:
        value = _non_empty_stripped(v, "source_platform")
        validate_source_platform(value)
        return value

    @field_validator("raw_text", mode="before")
    @classmethod
    def _validate_raw_text(cls, v: str) -> str:
        return _non_empty_preserved(v, "raw_text")

    @field_validator("text_canonicalization_version", mode="before")
    @classmethod
    def _validate_canonicalization_version(cls, v: str) -> str:
        return _non_empty_stripped(v, "text_canonicalization_version")

    @field_validator("raw_payload", mode="before")
    @classmethod
    def _validate_raw_payload_json(cls, v: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"raw_payload is not JSON-serializable: {exc}") from exc
        return v

    @field_validator("crawl_time")
    @classmethod
    def _validate_crawl_time(cls, v: datetime) -> datetime:
        ensure_timezone_aware(v)
        return v

    @model_validator(mode="after")
    def _ensure_content_hash(self) -> "CrawlerJDEnvelopeV1":
        expected = compute_content_hash(self.raw_text)
        if self.content_hash is None:
            self.content_hash = expected
        elif self.content_hash != expected:
            raise ValueError("content_hash does not match raw_text")
        return self
