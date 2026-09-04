from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.domain.tasks import TaskStatus


class TaskLogResponse(BaseModel):
    status: TaskStatus
    at: str
    message: str | None = None


class TaskRecordResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    task_id: str
    task_type: str
    status: str
    canonical_status: TaskStatus
    progress: float = Field(ge=0, le=1)
    input_payload: dict[str, JsonValue]
    result_payload: dict[str, JsonValue]
    result_reference: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_by: str | None = None
    attempt_count: int
    logs: list[TaskLogResponse]
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    implementation_status: str = "database_persisted_sync_executor"
    execution_mode: str
    rule_based: JsonValue = None
    provider: JsonValue = None
    algorithm_version: JsonValue = None
    capability_implementation_status: JsonValue = None


class TaskRecordEnvelope(BaseModel):
    code: Literal[0]
    message: str
    data: TaskRecordResponse
    trace_id: str


PortalDemoTaskType = Literal[
    "jd_extraction",
    "cv_extraction",
    "trend",
    "discovery",
    "matching",
]


class PortalDemoTaskError(BaseModel):
    code: str | None
    message: str | None


class PortalDemoTaskResponse(BaseModel):
    task_id: str
    task_type: PortalDemoTaskType
    object_type: str
    object_id: str
    service: str
    status: TaskStatus
    progress: float = Field(ge=0, le=1)
    error: PortalDemoTaskError | None
    result_reference: str | None
    created_at: datetime | None
    updated_at: datetime | None


class PortalDemoTaskCollectionEnvelope(BaseModel):
    code: Literal[0]
    message: str
    data: list[PortalDemoTaskResponse]
    trace_id: str
