from pydantic import BaseModel
from typing import Optional


class TaskStatus(BaseModel):
    task_id: str
    task_type: str
    status: str  # pending / running / completed / failed
    progress: Optional[str] = None
    result_count: int = 0
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class TaskListResponse(BaseModel):
    tasks: list[TaskStatus]
    total: int
