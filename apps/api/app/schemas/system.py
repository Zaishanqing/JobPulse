from typing import Any

from pydantic import BaseModel, Field, JsonValue, RootModel, field_validator


class SystemConfigUpdateRequest(RootModel[dict[str, JsonValue]]):
    """A named JSON-object request while allowing configuration-specific keys."""


class ModelServiceConfigRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("API address must start with http:// or https://")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()


class SystemStatusResponse(BaseModel):
    status: str
    app_name: str
    api_prefix: str
    components: dict[str, Any]


class ComponentStatusResponse(BaseModel):
    status: str
    detail: dict[str, Any]
