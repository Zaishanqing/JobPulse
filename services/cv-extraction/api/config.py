from __future__ import annotations

import os
from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.resource_manifest import ResourceManifestError, validate_resource_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESOURCE_MANIFEST_PATH = PROJECT_ROOT / "resources" / "cv-resource-manifest.v1.json"
DEFAULT_NORMALIZATION_PATH = (
    PROJECT_ROOT / "resources" / "normalization" / "2.0" / "normalization_map.yaml"
)
DEFAULT_SKILL_TAXONOMY_PATH = (
    PROJECT_ROOT / "resources" / "taxonomy" / "2.0" / "skill_taxonomy_snapshot.json"
)
DEFAULT_POSITION_TAXONOMY_PATH = (
    PROJECT_ROOT
    / "resources"
    / "taxonomy"
    / "position"
    / "3.0"
    / "position_taxonomy_catalog.v3.json"
)


class Settings(BaseSettings):
    PROVIDER: str = "deepseek"
    MODEL: str = "deepseek-v4-flash"
    PROMPT_VERSION: str = "cv-prompt.v1"
    SCHEMA_VERSION: str = "2.4"
    NORMALIZATION_VERSION: str = "2.0"
    RESOURCE_MANIFEST_PATH: str = str(DEFAULT_RESOURCE_MANIFEST_PATH)
    NORMALIZATION_PATH: str = str(DEFAULT_NORMALIZATION_PATH)
    SKILL_TAXONOMY_PATH: str = str(DEFAULT_SKILL_TAXONOMY_PATH)
    POSITION_TAXONOMY_PATH: str = str(DEFAULT_POSITION_TAXONOMY_PATH)
    POSITION_CLASSIFICATION_MAX_ATTEMPTS: int = Field(default=1, ge=1, le=5)
    CV_EXTRACTION_INTERNAL_TOKEN: str | None = None
    CV_EXTRACTION_MAX_WORKERS: int = Field(default=20, ge=1, le=64)
    CV_EXTRACTION_PARALLEL_SECTION_EXTRACTION: bool = True
    CV_EXTRACTION_SEMANTIC_RETRY_ATTEMPTS: int = Field(default=2, ge=0, le=2)
    CV_EXTRACTION_API_TIMEOUT_SECONDS: int = Field(default=60, ge=10, le=300)
    # 单次 HTTP 任务的 wall-clock 总预算；必须小于主后端 CV_EXTRACTION_READ_TIMEOUT_SECONDS，
    # 超预算时快速返回 504 让主后端队列重试并复用 checkpoint，而不是挂到被客户端掐断。
    CV_EXTRACTION_TASK_BUDGET_SECONDS: int = Field(default=570, ge=30, le=3600)
    CV_EXTRACTION_CHECKPOINT_PATH: str | None = None

    model_config = SettingsConfigDict(extra="ignore")

    @model_validator(mode="after")
    def validate_runtime(self):
        token = (self.CV_EXTRACTION_INTERNAL_TOKEN or "").strip()
        lowered = token.casefold()
        if len(token) < 32 or any(
            marker in lowered
            for marker in ("change-me", "placeholder", "default", "development")
        ):
            raise ValueError("CV_EXTRACTION_INTERNAL_TOKEN must be a strong internal token")
        try:
            validate_resource_manifest(
                self.RESOURCE_MANIFEST_PATH,
                normalization_path=self.NORMALIZATION_PATH,
                taxonomy_path=self.SKILL_TAXONOMY_PATH,
                normalization_version=self.NORMALIZATION_VERSION,
                cv_schema_version=self.SCHEMA_VERSION,
            )
        except ResourceManifestError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @property
    def llm_ready(self) -> bool:
        return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
