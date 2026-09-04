from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", extra="ignore", frozen=True)

    model_id: Literal["BAAI/bge-m3"] = "BAAI/bge-m3"
    model_revision: str = Field(
        default="5617a9f61b028005a4858fdac845db406aefb181",
        pattern=r"^[0-9a-f]{40}$",
    )
    dimension: int = Field(default=1024, ge=1024, le=1024)
    device: Literal["cpu", "cuda"] = "cpu"
    use_fp16: bool = False
    normalized: bool = True
    representation: Literal["dense"] = "dense"
    similarity: Literal["cosine"] = "cosine"
    batch_size: int = Field(default=16, ge=1, le=64)
    max_concurrency: int = Field(default=2, ge=1, le=32)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    max_input_count: int = Field(default=64, ge=1, le=64)
    max_text_chars: int = Field(default=4096, ge=1, le=4096)
    cache_dir: str = "/models"

    @field_validator("normalized")
    @classmethod
    def require_normalized_vectors(cls, value: bool) -> bool:
        if not value:
            raise ValueError("EMBEDDING_NORMALIZED must be true")
        return value


__all__ = ["Settings"]
