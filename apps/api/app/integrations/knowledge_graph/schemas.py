from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpstreamEnvelope(BaseModel):
    code: int
    message: str
    data: Any = None
    details: Any = Field(default_factory=dict)
    trace_id: str | None = None
    response_headers: dict[str, str] = Field(default_factory=dict, exclude=True)


class SyncResult(StrictModel):
    document_id: str
    knowledge_graph_id: str
    sync_version: str
    sync_status: str
    idempotent: bool = False
    upstream_trace_id: str | None = None
