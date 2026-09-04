import re
from contextvars import ContextVar
from uuid import uuid4


_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def create_trace_id(candidate: str | None = None) -> str:
    if candidate and _TRACE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return f"req_{uuid4().hex}"


def set_trace_id(trace_id: str):
    return _trace_id.set(trace_id)


def reset_trace_id(token) -> None:
    _trace_id.reset(token)


def get_trace_id() -> str:
    current = _trace_id.get()
    return current or create_trace_id()
