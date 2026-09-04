from app.contexts.platform import OCRResultRecord


def ocr_result_data(result: OCRResultRecord, task_id: str | None = None) -> dict[str, object]:
    data: dict[str, object] = {
        "result_id": result.result_id,
        "source_type": result.source_type,
        "filename": result.filename,
        "status": result.status,
        "text": result.text,
        "provider": result.provider,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "created_by": result.created_by,
        "edited": result.edited,
        "created_at": result.created_at.isoformat() if result.created_at else None,
        "updated_at": result.updated_at.isoformat() if result.updated_at else None,
        "implementation_status": "database_persisted_adapter_result",
    }
    if task_id is not None:
        data["task_id"] = task_id
    return data
