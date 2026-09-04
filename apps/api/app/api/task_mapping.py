from app.contexts.tasks import TaskRecord
from app.domain.values import thaw


def _rule_metadata_compat(result: dict) -> dict:
    """Map legacy mock-named rule metadata to formal deterministic names."""
    is_legacy_rule = (
        result.get("implementation_status") == "mock_keyword_jd_parse"
        or result.get("mock") is True
    )
    if not is_legacy_rule:
        return {}
    return {
        "execution_mode": "rule",
        "implementation_status": "deterministic_rule_jd_parse",
        "review_only": True,
    }


def task_data(task: TaskRecord) -> dict:
    result = thaw(task.result_payload.values)
    rule_compat = _rule_metadata_compat(result)
    data = {
        "task_id": task.task_id, "task_type": task.task_type,
        "status": "completed" if task.status == "succeeded" else task.status,
        "canonical_status": task.status, "progress": task.progress,
        "input_payload": thaw(task.input_payload.values), "result_payload": result,
        "result_reference": task.result_reference, "error_code": task.error_code,
        "error_message": task.error_message, "created_by": task.created_by,
        "attempt_count": task.attempt_count, "logs": [{"status": item.status, "at": item.at, "message": item.message} for item in task.logs],
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "implementation_status": (
            result.get("implementation_status", "database_persisted_sync_executor")
            if result.get("provider") == "trend_intelligence_http"
            else "database_persisted_sync_executor"
        ),
        "execution_mode": (
            "remote_async_polling"
            if result.get("provider") == "trend_intelligence_http"
            else rule_compat.get(
                "execution_mode", result.get("execution_mode", "synchronous_local")
            )
        ),
        "rule_based": result.get("rule_based"), "provider": result.get("provider"),
        "algorithm_version": result.get("algorithm_version"),
        "capability_implementation_status": rule_compat.get(
            "implementation_status", result.get("implementation_status")
        ),
    }
    if "mock" in result:
        data["mock"] = result.get("mock")
    if "review_only" in result or "review_only" in rule_compat:
        data["review_only"] = rule_compat.get(
            "review_only", result.get("review_only")
        )
    for key, value in result.items():
        data.setdefault(key, value)
    return data
