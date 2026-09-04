from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class ClusterTaskRequest(BaseModel):
    algorithm: Literal["emerge_v3_2"] = "emerge_v3_2"
    time_window_start: date | None = None
    time_window_end: date | None = None
    dataset_id: Literal["d5-short-window-main-v1-37585b4079dd"] | None = None
    jd_ids: list[str] = Field(default_factory=list, max_length=1000)
    max_samples: int | None = Field(default=None, ge=20, le=10000)


class ClusterTaskResponse(BaseModel):
    task_id: str
    status: str
    created_count: int


class PositionClusterResponse(BaseModel):
    cluster_id: str
    cluster_name: str
    algorithm: str
    time_window_start: date | None = None
    time_window_end: date | None = None
    sample_count: int
    core_skills: list[dict[str, Any]]
    representative_titles: list[str]
    representative_jd_ids: list[str]
    stability_score: float = Field(ge=0, le=1)
    growth_score: float = Field(ge=0, le=1)
    distance_from_existing_positions: float = Field(ge=0, le=1)
    status: str
