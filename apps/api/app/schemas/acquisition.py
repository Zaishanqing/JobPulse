from pydantic import BaseModel, Field


class AcquisitionJobCreateRequest(BaseModel):
    source: str = Field(min_length=1, max_length=32)
    keyword: str = Field(default="", max_length=255)
    city: str = Field(default="", max_length=64)
    pages: int = Field(default=5, ge=1, le=100)
