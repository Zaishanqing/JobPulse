"""统一持久化结果模型 — Boss / Company / Liepin 共用 (task 02 final)."""
from dataclasses import dataclass
from typing import Literal


@dataclass
class PersistenceResult:
    source_platform: str
    source_record_id: str
    status: Literal["saved", "failed"]
    error_code: str = ""
    error_message: str = ""


def classify_persistence_error(exc: Exception) -> str:
    """Map exception text to a stable error_code."""
    message = str(exc).lower()
    if "unknown column" in message:
        return "database_schema_error"
    if "json" in message or "not serializable" in message:
        return "raw_payload_serialization_failed"
    if "crawl_time" in message:
        return "crawl_time_invalid"
    if "source_record_id" in message:
        return "source_record_id_missing"
    return "database_write_error"
