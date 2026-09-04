from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.input_limits import MAX_BATCH_SIZE


class TrendBatchQuery(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


TrendSortOrder = Literal["asc", "desc"]


class PublicationGate(BaseModel):
    applicable: bool
    eligible: bool
    blockers: list[str]


class TrendDeliveryResource(BaseModel):
    """Stable fields shared by runs, predictions, and trend reports."""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal["trend-delivery.v1"]
    resource_type: Literal[
        "prediction_run",
        "position_skill_trend_run",
        "predicted_position",
        "trend_report",
    ]
    resource_id: str
    status: str
    progress: float = Field(ge=0, le=1)
    source_coverage: float | None = Field(default=None, ge=0, le=1)
    missing_sources: list[str]
    quality_flags: list[Any]
    evidence_references: list[Any]
    review_status: str | None
    review_task_id: str | None
    publication_gate: PublicationGate


class TrendPagination(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class TrendDeliveryCollection(BaseModel):
    schema_version: Literal["trend-delivery.v1"]
    items: list[TrendDeliveryResource]
    pagination: TrendPagination
    filters: dict[str, Any]
    sort: dict[str, str]
    not_found_ids: list[str]


class TrendDeliveryEnvelope(BaseModel):
    code: int
    message: str
    data: TrendDeliveryResource
    trace_id: str


class TrendDeliveryCollectionEnvelope(BaseModel):
    code: int
    message: str
    data: TrendDeliveryCollection
    trace_id: str
