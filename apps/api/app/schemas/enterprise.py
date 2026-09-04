from pydantic import BaseModel, Field


class EnterpriseCreateRequest(BaseModel):
    enterprise_name: str = Field(min_length=1, max_length=255)
    industry: str | None = None
    scale: str | None = None
    location: str | None = None
    description: str | None = None


class EnterpriseUpdateRequest(BaseModel):
    enterprise_name: str | None = Field(default=None, min_length=1, max_length=255)
    industry: str | None = None
    scale: str | None = None
    location: str | None = None
    description: str | None = None
    status: str | None = None
