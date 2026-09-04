from app.domain.json_types import FrozenJsonObject
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class FeedbackRecord:
    feedback_id: str
    feedback_type: str
    created_by: str
    payload: FrozenJsonObject
    status: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class FeedbackTarget:
    owner_id: str | None


class FeedbackRepository(Protocol):
    def add(self, feedback_type: str, created_by: str, payload: FrozenJsonObject) -> FeedbackRecord: ...
    def get(self, feedback_id: str) -> FeedbackRecord | None: ...
    def get_target(self, object_type: str, object_id: str) -> FeedbackTarget | None: ...
    def find_open_duplicate(
        self, created_by: str, object_type: str, object_id: str
    ) -> FeedbackRecord | None: ...
    def list_page(
        self,
        *,
        owner_id: str | None,
        status: str | None,
        feedback_type: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[FeedbackRecord], int]: ...
    def update(self, feedback_id: str, payload: FrozenJsonObject | None, status: str | None) -> FeedbackRecord: ...


class FeedbackUnitOfWork(Protocol):
    feedback: FeedbackRepository
    def __enter__(self) -> "FeedbackUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
