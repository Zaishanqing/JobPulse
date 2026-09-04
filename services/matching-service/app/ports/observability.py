"""Observability ports used by application orchestration."""

from __future__ import annotations

from typing import Any, Protocol


class MetricsCollector(Protocol):
    def increment(self, name: str, value: float = 1, **labels: str) -> None: ...

    def set_gauge(self, name: str, value: float, **labels: str) -> None: ...

    def observe(self, name: str, seconds: float, **labels: str) -> None: ...

    def render(self) -> str: ...


class EventLogger(Protocol):
    def event(self, event: str, **fields: Any) -> str: ...


class NullMetricsCollector:
    def increment(self, name: str, value: float = 1, **labels: str) -> None:
        return None

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        return None

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        return None

    def render(self) -> str:
        return ""


class NullEventLogger:
    def event(self, event: str, **fields: Any) -> str:
        return ""
