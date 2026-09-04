from typing import Any

from app.compatibility.jd import parse_result_payload
from app.domain.text_cleaning import clean_jd_text_for_display
from app.domain.json_types import thaw_json_object
from app.contexts.jd_lifecycle import (
    AbnormalSkill,
    DuplicateCheckBatch,
    DuplicateCheckResult,
    InflationCheckBatch,
    InflationCheckResult,
    JDBatch,
    JDCreated,
    JDDTO,
    JDExportFile,
    JDParseBatch,
    JDParseResultDTO,
    JDPublicationDTO,
    SimilarJD,
    SkillCatalogMappingResult,
    SkillReviewResult,
    TaskDTO,
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def jd_data(jd: JDDTO) -> dict[str, Any]:
    return {
        "jd_id": jd.id,
        "source_type": jd.source_type,
        "source_name": jd.source_name,
        "source_platform": jd.source_platform,
        "enterprise_id": jd.enterprise_id,
        "title": jd.title,
        "raw_text": jd.cleaned_text or clean_jd_text_for_display(jd.raw_text),
        "publish_date": jd.publish_date.isoformat() if jd.publish_date else None,
        "url": jd.url,
        "file_id": jd.file_id,
        "parse_status": jd.parse_status,
        "input_extraction_status": jd.input_extraction_status,
        "input_provider": jd.input_provider,
        "input_error_code": jd.input_error_code,
        "input_error_message": jd.input_error_message,
        "implementation_status": (
            "adapter_extracted_input"
            if jd.input_extraction_status == "completed"
            else "adapter_extraction_failed"
            if jd.input_extraction_status == "failed"
            else "direct_text_input"
        ),
        "copy_risk_score": jd.copy_risk_score,
        "inflation_score": jd.inflation_score,
        "is_downweighted": jd.is_downweighted,
        "created_at": _iso(jd.created_at),
        "updated_at": _iso(jd.updated_at),
        "source_jd_id": jd.source_jd_id,
        "source_jd_version_id": jd.source_jd_version_id,
        "extraction_task_id": jd.extraction_task_id,
    }


def parse_result_data(result: JDParseResultDTO) -> dict[str, Any]:
    return parse_result_payload(result)


def publication_data(publication: JDPublicationDTO) -> dict[str, Any]:
    return {
        "publication_id": publication.id,
        "published_fact_id": publication.id,
        "parse_result_id": publication.parse_result_id,
        "jd_id": publication.jd_id,
        "source_jd_id": publication.source_jd_id,
        "source_jd_version_id": publication.source_jd_version_id,
        "extraction_task_id": publication.extraction_task_id,
        "document_id": publication.document_id,
        "schema_version": publication.schema_version,
        "normalization_schema_version": publication.normalization_schema_version,
        "idempotency_key": publication.idempotency_key,
        "snapshot": thaw_json_object(publication.snapshot_payload),
        "outbox_event_id": publication.outbox_event_id,
        "outbox_status": publication.outbox_status,
        "published_by": publication.published_by,
        "created_at": publication.created_at.isoformat(),
    }


def _rule_metadata_compat(result_payload: dict[str, Any]) -> dict[str, Any]:
    """Map legacy mock-named rule metadata to the formal deterministic names.

    Persisted payloads are not mutated; this only affects API projection so old
    rows remain readable while new rows already use the formal metadata.
    """
    is_legacy_rule = (
        result_payload.get("implementation_status") == "mock_keyword_jd_parse"
        or result_payload.get("mock") is True
    )
    if not is_legacy_rule:
        return {}
    return {
        "execution_mode": "rule",
        "implementation_status": "deterministic_rule_jd_parse",
        "review_only": True,
    }


def task_data(task: TaskDTO) -> dict[str, Any]:
    result_payload = thaw_json_object(task.result_payload)
    rule_compat = _rule_metadata_compat(result_payload)
    data = {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": "completed" if task.status == "succeeded" else task.status,
        "canonical_status": task.status,
        "progress": task.progress,
        "input_payload": thaw_json_object(task.input_payload),
        "result_payload": result_payload,
        "result_reference": task.result_reference,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "created_by": task.created_by,
        "attempt_count": task.attempt_count,
        "logs": [
            {"status": item.status, "at": item.at, "message": item.message}
            for item in task.log_entries
        ],
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "implementation_status": "database_persisted_sync_executor",
        "execution_mode": rule_compat.get(
            "execution_mode", result_payload.get("execution_mode", "synchronous_local")
        ),
        "rule_based": result_payload.get("rule_based"),
        "provider": result_payload.get("provider"),
        "algorithm_version": result_payload.get("algorithm_version"),
        "capability_implementation_status": rule_compat.get(
            "implementation_status", result_payload.get("implementation_status")
        ),
    }
    if "mock" in result_payload:
        data["mock"] = result_payload.get("mock")
    if "review_only" in result_payload or "review_only" in rule_compat:
        data["review_only"] = rule_compat.get(
            "review_only", result_payload.get("review_only")
        )
    for key, value in result_payload.items():
        data.setdefault(key, value)
    return data


def similar_jd_data(item: SimilarJD) -> dict[str, Any]:
    return {
        "jd_id": item.jd_id,
        "similarity": item.similarity,
        "source_name": item.source_name,
        "text_overlap": item.text_overlap,
        "skill_overlap": item.skill_overlap,
        "length_similarity": item.length_similarity,
    }


def duplicate_data(result: DuplicateCheckResult) -> dict[str, Any]:
    return {
        "jd_id": result.jd_id,
        "copy_risk_score": result.copy_risk_score,
        "similar_jds": [similar_jd_data(item) for item in result.similar_jds],
        "recommended_action": result.recommended_action,
        "reason": result.reason,
    }


def abnormal_skill_data(item: AbnormalSkill) -> dict[str, Any]:
    return {"skill_id": item.skill_id, "skill_name": item.skill_name, "reason": item.reason}


def inflation_data(result: InflationCheckResult) -> dict[str, Any]:
    return {
        "jd_id": result.jd_id,
        "inflation_score": result.inflation_score,
        "abnormal_skills": [abnormal_skill_data(item) for item in result.abnormal_skills],
        "recommended_action": result.recommended_action,
        "mismatch_reasons": list(result.mismatch_reasons),
    }


def map_jd_output(value: Any) -> Any:
    if isinstance(value, JDDTO):
        return jd_data(value)
    if isinstance(value, JDParseResultDTO):
        return parse_result_data(value)
    if isinstance(value, JDPublicationDTO):
        return publication_data(value)
    if isinstance(value, JDExportFile):
        return {
            "filename": value.filename,
            "media_type": value.media_type,
            "content_base64": value.content_base64,
            "worksheets": list(value.worksheets),
        }
    if isinstance(value, TaskDTO):
        return task_data(value)
    if isinstance(value, JDCreated):
        return {
            "jd_id": value.jd_id,
            "parse_status": value.parse_status,
            "created_at": _iso(value.created_at),
        }
    if isinstance(value, JDBatch):
        return {"created_count": len(value.items), "items": [jd_data(item) for item in value.items]}
    if isinstance(value, JDParseBatch):
        return {"parsed_count": len(value.items), "items": [parse_result_data(item) for item in value.items]}
    if isinstance(value, DuplicateCheckResult):
        return duplicate_data(value)
    if isinstance(value, DuplicateCheckBatch):
        return {"checked_count": len(value.items), "items": [duplicate_data(item) for item in value.items]}
    if isinstance(value, InflationCheckResult):
        return inflation_data(value)
    if isinstance(value, InflationCheckBatch):
        return {"checked_count": len(value.items), "items": [inflation_data(item) for item in value.items]}
    if isinstance(value, SimilarJD):
        return similar_jd_data(value)
    if isinstance(value, SkillReviewResult):
        return {
            "jd_id": value.jd_id,
            "parse_result_id": value.parse_result_id,
            "skill_id": value.skill_id,
            "raw_skill": value.raw_skill,
            "normalized_skill_id": value.normalized_skill_id,
            "abnormal": value.abnormal,
            "abnormal_reason": value.abnormal_reason,
            "review_status": value.review_status,
            "implementation_status": value.implementation_status,
        }
    if isinstance(value, SkillCatalogMappingResult):
        return {
            "jd_id": value.jd_id,
            "parse_result_id": value.parse_result_id,
            "source_name": value.source_name,
            "requirement_id": value.requirement_id,
            "skill_id": value.skill_id,
            "canonical_name": value.canonical_name,
            "resolution_status": value.resolution_status,
            "closed_blocking_flags": value.closed_blocking_flags,
            "review_status": value.review_status,
        }
    if isinstance(value, (list, tuple)):
        return [map_jd_output(item) for item in value]
    return value
