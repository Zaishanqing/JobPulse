from __future__ import annotations

from math import ceil
from typing import Any

from app.contexts.tasks import TaskRecord
from app.api.task_mapping import task_data


SCHEMA_VERSION = "trend-delivery.v1"


def delivery_fields(
    *,
    resource_type: str,
    resource_id: str,
    status: str,
    progress: float,
    source_coverage: float | None = None,
    missing_sources=(),
    quality_flags=(),
    evidence_references=(),
    review_status: str | None = None,
    review_task_id: str | None = None,
    publishable: bool = False,
    publication_blockers=(),
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "progress": progress,
        "source_coverage": source_coverage,
        "missing_sources": list(missing_sources),
        "quality_flags": list(quality_flags),
        "evidence_references": list(evidence_references),
        "review_status": review_status,
        "review_task_id": review_task_id,
        "publication_gate": {
            "applicable": resource_type in {"predicted_position", "trend_report"},
            "eligible": publishable,
            "blockers": list(publication_blockers),
        },
    }


def trend_task_data(task: TaskRecord, resource_type: str) -> dict[str, Any]:
    value = task_data(task)
    result = value.get("result_payload") or {}
    value.update(delivery_fields(
        resource_type=resource_type,
        resource_id=task.task_id,
        status=str(value["status"]),
        progress=float(value["progress"]),
        source_coverage=result.get("source_coverage"),
        missing_sources=result.get("missing_sources") or (),
        quality_flags=result.get("quality_flags") or (),
        evidence_references=result.get("evidence_references") or (),
    ))
    return value


def collection_data(
    items: list[dict[str, Any]],
    *,
    page: int,
    page_size: int,
    filters: dict[str, Any],
    sort_by: str,
    sort_order: str,
    not_found_ids: list[str] | None = None,
) -> dict[str, Any]:
    total = len(items)
    start = (page - 1) * page_size
    return {
        "schema_version": SCHEMA_VERSION,
        "items": items[start : start + page_size],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": ceil(total / page_size) if total else 0,
        },
        "filters": filters,
        "sort": {"by": sort_by, "order": sort_order},
        "not_found_ids": not_found_ids or [],
    }
