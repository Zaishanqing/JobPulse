"""Allowlisted JSON logs that cannot serialize profiles, vectors or credentials."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.domain.privacy import find_pii

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_ALLOWED_FIELDS = {
    "request_id",
    "correlation_id",
    "method",
    "path",
    "http_status",
    "task_id",
    "evaluation_id",
    "access_scope",
    "algorithm_version",
    "config_version",
    "duration_ms",
    "status",
    "error_code",
    "worker_id",
    "outcome",
    "operation",
    "model_id",
    "model_revision",
    "dimension",
    "trace_id",
    "actor_id",
    "auth_decision",
}
_IDENTIFIER_FIELDS = {
    "request_id",
    "correlation_id",
    "task_id",
    "evaluation_id",
    "access_scope",
    "worker_id",
    "actor_id",
    "model_id",
    "model_revision",
    "trace_id",
}


class StructuredLogger:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("matching_service")

    def event(self, event: str, **fields: Any) -> str:
        payload: dict[str, Any] = {
            "event": event,
            # Whole-second precision keeps logs stable and avoids leaking arbitrary
            # numeric fragments through timestamp microseconds in redaction checks.
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for key, value in fields.items():
            if key not in _ALLOWED_FIELDS or value is None:
                continue
            if key in _IDENTIFIER_FIELDS:
                value = safe_identifier(value)
            elif isinstance(value, str):
                value = "[redacted]" if find_pii(value) else value[:200]
            elif not isinstance(value, int | float | bool):
                continue
            payload[key] = value
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self._logger.info(rendered)
        return rendered


def safe_identifier(value: Any) -> str:
    text = str(value)
    return text if _SAFE_IDENTIFIER.fullmatch(text) and not find_pii(text) else "[redacted]"
