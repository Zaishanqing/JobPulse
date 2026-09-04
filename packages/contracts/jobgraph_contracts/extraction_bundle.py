"""Extraction → downstream JD bundle (V1).

:class:`ExtractedJDBundleV1` wraps the two V2 outputs (extraction +
normalization) together with identity, provider, and run metadata so that
the main framework can validate, store, and publish the results.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_v2 import JDExtractionResult
from jobgraph_contracts.normalization_v2 import JDNormalizedResult
from jobgraph_contracts.source_identity import (
    ensure_timezone_aware,
    normalize_source_record_id,
    validate_source_platform,
)
from jobgraph_contracts.skill_taxonomy import SkillTaxonomyProjectionV1


# ---------------------------------------------------------------------------
# Re-usable validators
# ---------------------------------------------------------------------------


def _non_empty_stripped(v: str, label: str) -> str:
    if not isinstance(v, str):
        raise TypeError(f"{label} must be a string")
    stripped = v.strip()
    if not stripped:
        raise ValueError(f"{label} must be non-empty after stripping whitespace")
    return stripped


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


class ExtractedJDBundleV1(StrictContract):
    """Extraction output bundle — the single return value of ``extract_one()``.

    Each bundle carries the full extraction and normalisation results for one
    source JD, together with identity fields that allow the main framework to
    trace the result back to a specific :class:`CrawlerJDEnvelopeV1`.
    """

    schema_version: Literal["extracted-jd-bundle-v1"] = "extracted-jd-bundle-v1"

    # --- source identity (must match the input envelope) --------------------
    source_platform: str
    source_record_id: str
    source_version: str = "1"

    # --- canonical cleaned source text used as the Evidence base ------------
    cleaned_text: str

    # --- V2 results --------------------------------------------------------
    extraction_result: JDExtractionResult
    normalized_result: JDNormalizedResult

    # --- review artefacts --------------------------------------------------
    review_flags: list[dict[str, Any]] = Field(default_factory=list)

    # --- extraction run metadata -------------------------------------------
    extraction_provider: str
    model_version: str
    extraction_run_id: str
    extraction_started_at: datetime
    extraction_finished_at: datetime

    # == validators =========================================================

    @field_validator("source_platform", mode="before")
    @classmethod
    def _validate_source_platform(cls, v: str) -> str:
        value = _non_empty_stripped(v, "source_platform")
        validate_source_platform(value)
        return value

    @field_validator("source_record_id", mode="before")
    @classmethod
    def _validate_source_record_id(cls, v: str) -> str:
        if not isinstance(v, str):
            raise TypeError(f"source_record_id must be a string, got {type(v)}")
        return normalize_source_record_id(v)

    @field_validator("cleaned_text", mode="before")
    @classmethod
    def _validate_cleaned_text(cls, v: object) -> str:
        if not isinstance(v, str):
            raise TypeError("cleaned_text must be a string")
        if not v.strip():
            raise ValueError("cleaned_text must not be empty")
        return v

    @field_validator("extraction_provider", mode="before")
    @classmethod
    def _validate_extraction_provider(cls, v: str) -> str:
        return _non_empty_stripped(v, "extraction_provider")

    @field_validator("model_version", mode="before")
    @classmethod
    def _validate_model_version(cls, v: str) -> str:
        return _non_empty_stripped(v, "model_version")

    @field_validator("extraction_run_id", mode="before")
    @classmethod
    def _validate_extraction_run_id(cls, v: str) -> str:
        return _non_empty_stripped(v, "extraction_run_id")

    @field_validator("extraction_started_at")
    @classmethod
    def _validate_started_at(cls, v: datetime) -> datetime:
        ensure_timezone_aware(v)
        return v

    @field_validator("extraction_finished_at")
    @classmethod
    def _validate_finished_at(cls, v: datetime) -> datetime:
        ensure_timezone_aware(v)
        return v

    @model_validator(mode="after")
    def _validate_document_ids_match(self) -> "ExtractedJDBundleV1":
        eid = self.extraction_result.document_id
        nid = self.normalized_result.document_id
        if eid != nid:
            raise ValueError(
                f"extraction_result.document_id ({eid!r}) "
                f"!= normalized_result.document_id ({nid!r})"
            )
        return self

    @model_validator(mode="after")
    def _validate_time_order(self) -> "ExtractedJDBundleV1":
        if self.extraction_finished_at < self.extraction_started_at:
            raise ValueError(
                f"extraction_finished_at ({self.extraction_finished_at}) "
                f"is earlier than extraction_started_at ({self.extraction_started_at})"
            )
        return self

    @model_validator(mode="after")
    def _validate_review_flags_serializable(self) -> "ExtractedJDBundleV1":
        try:
            json.dumps(self.review_flags, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"review_flags is not JSON-serializable: {exc}") from exc
        return self


class JDExtractionExecutionV2(StrictContract):
    mode: Literal["llm", "rule"]
    provider: str
    model: str
    prompt_version: str
    algorithm_version: str
    schema_version: str
    normalization_version: str
    started_at: datetime
    finished_at: datetime

    @model_validator(mode="after")
    def validate_time_order(self) -> "JDExtractionExecutionV2":
        ensure_timezone_aware(self.started_at)
        ensure_timezone_aware(self.finished_at)
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        return self


class ExtractedJDBundleV2(ExtractedJDBundleV1):
    """V2 adds an authoritative catalog classification projection."""

    schema_version: Literal["extracted-jd-bundle-v2"] = "extracted-jd-bundle-v2"
    skill_taxonomy: SkillTaxonomyProjectionV1
    execution: JDExtractionExecutionV2 | None = None
    need_review: bool = False
    confidence_level: Literal["standard", "limited"] = "standard"


def parse_extracted_jd_bundle(payload: Any) -> ExtractedJDBundleV1 | ExtractedJDBundleV2:
    if isinstance(payload, (ExtractedJDBundleV1, ExtractedJDBundleV2)):
        return payload
    if not isinstance(payload, dict):
        raise TypeError("extraction bundle must be an object")
    version = payload.get("schema_version")
    if version == "extracted-jd-bundle-v1":
        return ExtractedJDBundleV1.model_validate(payload)
    if version == "extracted-jd-bundle-v2":
        return ExtractedJDBundleV2.model_validate(payload)
    raise ValueError(f"unsupported extraction bundle schema_version: {version!r}")


# ---------------------------------------------------------------------------
# Cross-contract identity validation
# ---------------------------------------------------------------------------


def validate_bundle_matches_envelope(
    envelope: CrawlerJDEnvelopeV1,
    bundle: ExtractedJDBundleV1,
) -> None:
    """Verify that *bundle* was produced from *envelope*.

    Raises :class:`ValueError` with a field-specific message when any of
    ``source_platform``, ``source_record_id`` or ``source_version`` differ
    between the two contracts.  Callers in Extraction and the main framework
    MUST invoke this before persisting or publishing bundle results.
    """
    if bundle.source_platform != envelope.source_platform:
        raise ValueError(
            f"source_platform mismatch: "
            f"bundle={bundle.source_platform!r}, envelope={envelope.source_platform!r}"
        )
    if bundle.source_record_id != envelope.source_record_id:
        raise ValueError(
            f"source_record_id mismatch: "
            f"bundle={bundle.source_record_id!r}, envelope={envelope.source_record_id!r}"
        )
    if bundle.source_version != envelope.source_version:
        raise ValueError(
            f"source_version mismatch: "
            f"bundle={bundle.source_version!r}, envelope={envelope.source_version!r}"
        )
