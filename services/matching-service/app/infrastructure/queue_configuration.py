"""Environment-driven TaskQueue selection."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.infrastructure.memory_task_queue import InMemoryTaskQueue
from app.infrastructure.redis_task_queue import RedisTaskQueue
from app.ports.task_queue import TaskQueue


@dataclass(frozen=True)
class QueueSelection:
    provider: str
    queue: TaskQueue
    visibility_timeout_seconds: float
    retry_interval_seconds: float


def build_task_queue(environment: Mapping[str, str] | None = None) -> QueueSelection:
    env = environment if environment is not None else os.environ
    provider = env.get("MATCHING_QUEUE_PROVIDER", "memory").strip().lower()
    visibility = _positive_float(
        env.get("MATCHING_QUEUE_VISIBILITY_TIMEOUT_SECONDS", "60"),
        "MATCHING_QUEUE_VISIBILITY_TIMEOUT_SECONDS",
    )
    retry_interval = _non_negative_float(
        env.get("MATCHING_QUEUE_RETRY_INTERVAL_SECONDS", "5"),
        "MATCHING_QUEUE_RETRY_INTERVAL_SECONDS",
    )
    if provider == "memory":
        queue = InMemoryTaskQueue(visibility_timeout_seconds=visibility)
    elif provider == "redis":
        redis_url = env.get("MATCHING_REDIS_URL", "").strip()
        if not redis_url:
            raise ValueError("MATCHING_REDIS_URL is required for the Redis queue provider")
        queue = RedisTaskQueue.from_url(
            redis_url,
            queue_name=env.get("MATCHING_QUEUE_NAME", "matching:evaluation-tasks"),
            visibility_timeout_seconds=visibility,
            socket_timeout_seconds=_positive_float(
                env.get("MATCHING_REDIS_TIMEOUT_SECONDS", "5"),
                "MATCHING_REDIS_TIMEOUT_SECONDS",
            ),
        )
    else:
        raise ValueError("MATCHING_QUEUE_PROVIDER must be memory or redis")
    return QueueSelection(provider, queue, visibility, retry_interval)


def _positive_float(raw: str, name: str) -> float:
    value = _non_negative_float(raw, name)
    if value == 0:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_float(raw: str, name: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value
