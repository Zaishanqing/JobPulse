from __future__ import annotations

import os
from dataclasses import dataclass, field


def _positive_int(name: str, default: int, errors: list[str]) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer")
        return default
    if value < 1:
        errors.append(f"{name} must be positive")
        return default
    return value


@dataclass(frozen=True)
class ExtractionAPISettings:
    internal_token: str | None
    model: str = "deepseek-v4-flash"
    normalization_path: str = "config/normalization_map.yaml"
    skill_taxonomy_path: str = "config/skill_taxonomy_snapshot.json"
    position_taxonomy_path: str = "config/position_taxonomy_catalog.v3.json"
    position_classification_max_attempts: int = 3
    extraction_provider: str = "deepseek"
    prompt_version: str = "jd-prompt.v1"
    algorithm_version: str = "jd-llm-extraction.v1"
    normalization_version: str = "v2"
    max_concurrency: int = 4
    max_request_bytes: int = 2 * 1024 * 1024
    configuration_errors: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls) -> "ExtractionAPISettings":
        errors: list[str] = []
        token = os.getenv("JD_EXTRACTION_INTERNAL_TOKEN")
        if token is not None:
            token = token.strip() or None
        if token is None:
            errors.append("JD_EXTRACTION_INTERNAL_TOKEN is required")
        concurrency = _positive_int("JD_EXTRACTION_MAX_CONCURRENCY", 4, errors)
        request_bytes = _positive_int("JD_EXTRACTION_MAX_REQUEST_BYTES", 2 * 1024 * 1024, errors)
        position_attempts = _positive_int(
            "JD_POSITION_CLASSIFICATION_MAX_ATTEMPTS",
            3,
            errors,
        )
        return cls(
            internal_token=token,
            model=os.getenv("JD_EXTRACTION_MODEL", "deepseek-v4-flash").strip(),
            normalization_path=os.getenv(
                "JD_EXTRACTION_NORMALIZATION_PATH",
                "config/normalization_map.yaml",
            ).strip(),
            skill_taxonomy_path=os.getenv(
                "JD_EXTRACTION_SKILL_TAXONOMY_PATH",
                "config/skill_taxonomy_snapshot.json",
            ).strip(),
            position_taxonomy_path=os.getenv(
                "JD_POSITION_TAXONOMY_PATH",
                "config/position_taxonomy_catalog.v3.json",
            ).strip(),
            position_classification_max_attempts=position_attempts,
            extraction_provider=os.getenv("JD_EXTRACTION_PROVIDER", "deepseek").strip(),
            prompt_version=os.getenv("JD_EXTRACTION_PROMPT_VERSION", "jd-prompt.v1").strip(),
            algorithm_version=os.getenv(
                "JD_EXTRACTION_ALGORITHM_VERSION", "jd-llm-extraction.v1"
            ).strip(),
            normalization_version=os.getenv(
                "JD_EXTRACTION_NORMALIZATION_VERSION", "v2"
            ).strip(),
            max_concurrency=concurrency,
            max_request_bytes=request_bytes,
            configuration_errors=tuple(errors),
        )
