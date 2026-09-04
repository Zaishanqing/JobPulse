from collections.abc import Mapping
from typing import Protocol

from app.domain.value_types import SerializedPayload


class ReviewObjectHandler(Protocol):
    def apply(
        self, object_id: str, build_run_id: int | None, action: str
    ) -> SerializedPayload: ...


class ReviewObjectHandlerRegistry:
    def __init__(
        self, handlers: Mapping[str, ReviewObjectHandler], default: ReviewObjectHandler
    ):
        self._handlers = dict(handlers)
        self._default = default

    def handler_for(self, object_type: str) -> ReviewObjectHandler:
        return self._handlers.get(object_type, self._default)
