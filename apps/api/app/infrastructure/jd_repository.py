from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import cast
from uuid import uuid4

from sqlalchemy import case, func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.integrations.registry import get_integration_registry
from app.models.enterprise import Enterprise
from app.models.file_asset import FileAsset
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.jd_publication import JDPublication
from app.models.outbox_message import OutboxMessage
from app.models.review_task import ReviewTask
from app.models.review_task_event import ReviewTaskEvent
from app.models.source_jd import SourceJD, SourceJDVersion
from app.models.extraction_task import ExtractionTask
from app.models.standard_position import StandardPosition
from app.models.task_record import TaskRecord
from app.contexts.jd_lifecycle import (
    Actor,
    FileAssetDTO,
    FileRepository,
    FileTextExtractor,
    FileTextExtractionResult,
    FileUpload,
    JDCreateCommand,
    JDCopyRiskUpdate,
    JDDTO,
    JDDownweightUpdate,
    JDInflationUpdate,
    JDLegacyFields,
    JDParseResultDTO,
    JDPublicationDTO,
    JDParseResultCreateCommand,
    JDParseResultResetUpdate,
    JDParseResultReviewUpdate,
    JDParseResultUpdateCommand,
    JDParseResultVersionedUpdate,
    JDParseStatusUpdate,
    JDRepository,
    JDRawTextUpdate,
    JDSchemaView,
    JDSkillDTO,
    JDSummaryDTO,
    JDUpdateCommand,
    JDUoW,
    TaskDTO,
    TaskLogDTO,
    TaskRepository,
    TaskStatus,
)
from app.domain.json_types import (
    FrozenJsonArray,
    InvalidJsonValue,
    JsonObject,
    JsonValue,
    MutableJsonObject,
    freeze_json,
    freeze_json_object,
    thaw_json_object,
)
from app.infrastructure.file_extraction import extract_file_text
from app.compatibility.jd import parse_result_payload
from app.domain.jd_policies import JDParseCommand
from app.domain.jd_skill_catalog import CatalogAlias, CatalogIdentity, CatalogSkill
from app.infrastructure.outbox import SqlAlchemyOutboxRepository
from app.integration_events import (
    IdempotencyKey,
    IntegrationEvent,
    OutboxMessageDraft,
)
from app.domain.jd_publication import publication_idempotency_key
from app.models.user import utc_now
from jobgraph_contracts.source_identity import compute_content_hash


ALLOWED_UPLOAD_TYPES: dict[str, set[str]] = {
    "application/pdf": {".pdf"},
    "application/msword": {".doc"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "text/plain": {".txt"},
}


def _content_hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_result_catalog_identity(
    parsed: JDParseResult,
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    metadata = parsed.execution_metadata
    if not isinstance(metadata, Mapping):
        return None, None
    identity = metadata.get("catalog_identity")
    if not isinstance(identity, Mapping):
        return None, None

    def valid(value: object) -> dict[str, str] | None:
        if not isinstance(value, Mapping):
            return None
        version = value.get("catalog_version")
        content_hash = value.get("content_hash")
        if not isinstance(version, str) or not version:
            return None
        if not isinstance(content_hash, str) or not content_hash:
            return None
        return {"catalog_version": version, "content_hash": content_hash}

    return valid(identity.get("skill")), valid(identity.get("position"))


def _catalog_snapshot(session: Session, effective_at: datetime) -> dict[str, str]:
    from app.infrastructure.data_validation import frozen_catalog_identity

    identity = frozen_catalog_identity(session)
    return {
        "source": "main-system-skill-catalog",
        "catalog_version": identity["catalog_version"],
        "content_hash": identity["content_hash"],
        "effective_at": effective_at.isoformat(),
        "status": "active",
    }


def _position_catalog_snapshot(
    session: Session, effective_at: datetime
) -> dict[str, str]:
    from app.infrastructure.data_validation import (
        frozen_position_catalog_identity,
    )

    identity = frozen_position_catalog_identity(session)
    return {
        "source": "main-system-position-catalog",
        "catalog_version": identity["catalog_version"],
        "content_hash": identity["content_hash"],
        "effective_at": effective_at.isoformat(),
        "status": "active",
    }


class JDDataMappingError(ValueError):
    """A persisted JD value cannot be represented by the typed Port contract."""


def _json_value(value: object, *, field: str) -> JsonValue:
    try:
        return freeze_json(value, field=field)
    except InvalidJsonValue as exc:
        raise JDDataMappingError(str(exc)) from exc


def _json_object(value: object, *, field: str) -> JsonObject:
    try:
        return freeze_json_object(value, field=field)
    except InvalidJsonValue as exc:
        raise JDDataMappingError(str(exc)) from exc


def _json_persistence_object(value: object, *, field: str) -> MutableJsonObject:
    """Validate independently at the ORM boundary, then thaw a defensive copy."""
    return thaw_json_object(_json_object(value, field=field))


def _publication_normalization_projection(value: object) -> MutableJsonObject:
    normalized = _json_persistence_object(value, field="normalized_result")
    requirements = normalized.get("normalized_requirements")
    if not isinstance(requirements, list):
        raise JDDataMappingError("normalized_result.normalized_requirements must be an array")
    eligible = [
        item
        for item in requirements
        if isinstance(item, dict)
        and item.get("resolution_status") in {"resolved", "manually_confirmed"}
        and item.get("skill_id")
        and item.get("canonical_name")
    ]
    excluded_count = len(requirements) - len(eligible)
    unresolved = normalized.get("unresolved_items")
    if not isinstance(unresolved, list):
        raise JDDataMappingError("normalized_result.unresolved_items must be an array")
    normalized["normalized_requirements"] = eligible
    normalized["unresolved_items"] = [
        item
        for item in unresolved
        if isinstance(item, dict) and item.get("item_type") != "skill"
    ]
    normalized["projection"] = {
        "policy": "resolved-skills-only.v1",
        "excluded_skill_count": excluded_count,
    }
    return normalized


def _eligible_legacy_skills(
    value: object, *, field: str, eligible_skill_ids: set[str]
) -> list[object]:
    if not isinstance(value, list):
        raise JDDataMappingError(f"{field} must be an array")
    return [
        item
        for item in value
        if isinstance(item, dict)
        and item.get("normalized_skill_id") in eligible_skill_ids
    ]


def _require_json_array(value: object, *, field: str) -> FrozenJsonArray:
    if not isinstance(value, (list, FrozenJsonArray)):
        raise JDDataMappingError(
            f"{field} must be a JSON array, got {type(value).__name__}"
        )
    converted = _json_value(value, field=field)
    if not isinstance(converted, FrozenJsonArray):
        raise JDDataMappingError(f"{field} must be a JSON array")
    return converted


def _require_string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    items = _require_json_array(value, field=field)
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise JDDataMappingError(
                f"{field}[{index}] must be str, got {type(item).__name__}"
            )
    return tuple(items)


def _skill_dto(value: object, *, field: str) -> JDSkillDTO:
    if not isinstance(value, Mapping):
        raise JDDataMappingError(f"{field} items must be JSON objects")
    raw_skill = value.get("raw_skill")
    normalized_skill_id = value.get("normalized_skill_id")
    confidence = value.get("confidence")
    resolution_status = value.get("resolution_status")
    if not isinstance(raw_skill, str) or not raw_skill:
        raise JDDataMappingError(f"{field}.raw_skill is required and must be a string")
    if normalized_skill_id is not None and not isinstance(normalized_skill_id, str):
        raise JDDataMappingError(f"{field}.normalized_skill_id must be a string or null")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise JDDataMappingError(f"{field}.confidence is required and must be numeric")
    if resolution_status is not None and not isinstance(resolution_status, str):
        raise JDDataMappingError(f"{field}.resolution_status must be a string or null")
    try:
        return JDSkillDTO(
            raw_skill,
            normalized_skill_id,
            float(confidence),
            resolution_status,
        )
    except ValueError as exc:
        raise JDDataMappingError(f"{field}.confidence is outside the valid range") from exc


def _skill_values(value: JDSkillDTO) -> MutableJsonObject:
    return {
        "raw_skill": value.raw_skill,
        "normalized_skill_id": value.normalized_skill_id,
        "confidence": value.confidence,
        "resolution_status": value.resolution_status,
    }


def _legacy_values(legacy: JDLegacyFields) -> dict[str, object]:
    return {
        "position_title": legacy.position_title,
        "responsibilities": list(legacy.responsibilities),
        "required_skills": [_skill_values(item) for item in legacy.required_skills],
        "bonus_skills": [_skill_values(item) for item in legacy.bonus_skills],
        "education": legacy.education,
        "experience": legacy.experience,
        "industry": legacy.industry,
        "tools": list(legacy.tools),
    }


def _jd_create_values(command: JDCreateCommand) -> dict[str, object]:
    if not isinstance(command, JDCreateCommand):
        raise TypeError("command must be JDCreateCommand")
    return {
        "source_type": command.source_type,
        "source_name": command.source_name,
        "enterprise_id": command.enterprise_id,
        "title": command.title,
        "raw_text": command.raw_text,
        "cleaned_text": command.cleaned_text,
        "publish_date": command.publish_date,
        "url": command.url,
        "file_id": command.file_id,
        "parse_status": command.parse_status,
        "input_extraction_status": command.input_extraction_status,
        "input_provider": command.input_provider,
        "input_error_code": command.input_error_code,
        "input_error_message": command.input_error_message,
    }


def _jd_update_values(command: object) -> dict[str, object]:
    if isinstance(command, JDParseStatusUpdate):
        return {"parse_status": command.parse_status}
    if isinstance(command, JDRawTextUpdate):
        return {
            "raw_text": command.raw_text,
            "parse_status": command.parse_status,
            "input_extraction_status": command.input_extraction_status,
            "input_provider": command.input_provider,
            "input_error_code": command.input_error_code,
            "input_error_message": command.input_error_message,
        }
    if isinstance(command, JDCopyRiskUpdate):
        return {"copy_risk_score": command.copy_risk_score}
    if isinstance(command, JDInflationUpdate):
        return {"inflation_score": command.inflation_score}
    if isinstance(command, JDDownweightUpdate):
        return {"is_downweighted": command.is_downweighted}
    raise TypeError(f"Unsupported JD update command: {type(command).__name__}")


def _parse_create_values(command: JDParseResultCreateCommand) -> dict[str, object]:
    if not isinstance(command, JDParseResultCreateCommand):
        raise TypeError("command must be JDParseResultCreateCommand")
    return {
        "jd_id": command.jd_id,
        **_legacy_values(command.legacy),
        "business_scenarios": list(command.business_scenarios),
        "parse_confidence": command.parse_confidence,
        "need_review": command.need_review,
        "extraction_result": (
            _json_persistence_object(command.extraction_result, field="extraction_result")
            if command.extraction_result is not None else None
        ),
        "normalized_result": (
            _json_persistence_object(command.normalized_result, field="normalized_result")
            if command.normalized_result is not None else None
        ),
        "execution_metadata": (
            _json_persistence_object(
                command.execution_metadata, field="execution_metadata"
            )
            if command.execution_metadata is not None
            else None
        ),
        "schema_version": command.schema_version,
        "normalization_schema_version": command.normalization_schema_version,
        "workflow_status": command.workflow_status,
    }


def _parse_update_values(command: JDParseResultUpdateCommand) -> dict[str, object]:
    if isinstance(command, JDParseResultReviewUpdate):
        values: dict[str, object] = {}
        if command.parse_confidence is not None:
            values["parse_confidence"] = command.parse_confidence
        if command.need_review is not None:
            values["need_review"] = command.need_review
        if command.workflow_status is not None:
            values["workflow_status"] = command.workflow_status
        return values
    if isinstance(command, JDParseResultVersionedUpdate):
        values = {
            **_legacy_values(command.legacy),
            "extraction_result": _json_persistence_object(
                command.extraction_result, field="extraction_result"
            ),
            "normalized_result": _json_persistence_object(
                command.normalized_result, field="normalized_result"
            ),
            "schema_version": command.schema_version,
            "normalization_schema_version": command.normalization_schema_version,
            "need_review": command.need_review,
            "workflow_status": command.workflow_status,
        }
        if command.execution_metadata is not None:
            values["execution_metadata"] = _json_persistence_object(
                command.execution_metadata, field="execution_metadata"
            )
        return values
    if isinstance(command, JDParseResultResetUpdate):
        return {
            "extraction_result": None,
            "normalized_result": None,
            "workflow_status": command.workflow_status,
            "need_review": command.need_review,
        }
    raise TypeError(f"Unsupported parse update command: {type(command).__name__}")


def _jd_dto(jd: JobDescription, *, source_platform: str | None = None) -> JDDTO:
    return JDDTO(
        id=jd.id,
        source_type=jd.source_type,
        source_name=jd.source_name,
        enterprise_id=jd.enterprise_id,
        title=jd.title,
        raw_text=jd.raw_text,
        publish_date=jd.publish_date,
        url=jd.url,
        file_id=jd.file_id,
        parse_status=jd.parse_status,
        input_extraction_status=jd.input_extraction_status,
        input_provider=jd.input_provider,
        input_error_code=jd.input_error_code,
        input_error_message=jd.input_error_message,
        copy_risk_score=jd.copy_risk_score,
        inflation_score=jd.inflation_score,
        is_downweighted=jd.is_downweighted,
        created_at=jd.created_at,
        updated_at=jd.updated_at,
        source_jd_id=jd.source_jd_id,
        source_jd_version_id=jd.source_jd_version_id,
        extraction_task_id=jd.extraction_task_id,
        cleaned_text=jd.cleaned_text,
        source_platform=source_platform,
    )


def _parse_dto(result: JDParseResult) -> JDParseResultDTO:
    required_skills = _require_json_array(
        result.required_skills, field="required_skills"
    )
    bonus_skills = _require_json_array(result.bonus_skills, field="bonus_skills")
    try:
        return JDParseResultDTO(
        id=result.id,
        jd_id=result.jd_id,
        position_title=result.position_title,
        responsibilities=_require_string_tuple(
            result.responsibilities, field="responsibilities"
        ),
        required_skills=tuple(
            _skill_dto(item, field="required_skills")
            for item in required_skills
        ),
        bonus_skills=tuple(
            _skill_dto(item, field="bonus_skills")
            for item in bonus_skills
        ),
        education=result.education,
        experience=result.experience,
        industry=result.industry,
        tools=_require_string_tuple(result.tools, field="tools"),
        business_scenarios=_require_string_tuple(
            result.business_scenarios, field="business_scenarios"
        ),
        parse_confidence=result.parse_confidence,
        need_review=result.need_review,
        extraction_result=(
            _json_object(result.extraction_result, field="extraction_result")
            if result.extraction_result is not None else None
        ),
        normalized_result=(
            _json_object(result.normalized_result, field="normalized_result")
            if result.normalized_result is not None else None
        ),
        execution_metadata=(
            _json_object(result.execution_metadata, field="execution_metadata")
            if result.execution_metadata is not None
            else None
        ),
        schema_version=result.schema_version,
        normalization_schema_version=result.normalization_schema_version,
        workflow_status=result.workflow_status,
        created_at=result.created_at,
            updated_at=result.updated_at,
        )
    except ValueError as exc:
        raise JDDataMappingError(f"Invalid JD parse result: {exc}") from exc


def _file_dto(file_asset: FileAsset) -> FileAssetDTO:
    return FileAssetDTO(
        id=file_asset.id,
        owner_user_id=file_asset.owner_user_id,
        filename=file_asset.filename,
        content_type=file_asset.content_type,
        path=file_asset.path,
        size=file_asset.size,
        purpose=file_asset.purpose,
    )


def _task_dto(task: TaskRecord) -> TaskDTO:
    log_entries = _require_json_array(task.log_entries, field="log_entries")
    try:
        return TaskDTO(
        id=task.id,
        task_type=task.task_type,
        status=cast(TaskStatus, task.status),
        progress=task.progress,
        input_payload=_json_object(task.input_payload, field="input_payload"),
        result_payload=_json_object(task.result_payload, field="result_payload"),
        result_reference=task.result_reference,
        error_code=task.error_code,
        error_message=task.error_message,
        created_by=task.created_by,
        attempt_count=task.attempt_count,
        log_entries=tuple(_task_log_dto(item) for item in log_entries),
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
            finished_at=task.finished_at,
        )
    except ValueError as exc:
        raise JDDataMappingError(f"Invalid task record: {exc}") from exc


def _task_log_dto(value: object) -> TaskLogDTO:
    if not isinstance(value, Mapping):
        raise JDDataMappingError("log_entries items must be JSON objects")
    status = value.get("status")
    at = value.get("at")
    message = value.get("message")
    if not isinstance(status, str) or not isinstance(at, str):
        raise JDDataMappingError("Task log status and at are required strings")
    if message is not None and not isinstance(message, str):
        raise JDDataMappingError("Task log message must be a string or null")
    try:
        return TaskLogDTO(cast(TaskStatus, status), at, message)
    except ValueError as exc:
        raise JDDataMappingError(f"Invalid task log: {exc}") from exc


class SqlAlchemyJDRepository(JDRepository):
    """SQLAlchemy adapter for JD persistence. It never commits by itself."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _source_platforms(self, rows: list[JobDescription]) -> dict[str, str]:
        source_ids = {row.source_jd_id for row in rows if row.source_jd_id}
        if not source_ids:
            return {}
        return {
            source_id: platform
            for source_id, platform in self._session.query(
                SourceJD.id, SourceJD.source_platform
            ).filter(SourceJD.id.in_(source_ids)).all()
        }

    def _jd_dtos(self, rows: list[JobDescription]) -> list[JDDTO]:
        platforms = self._source_platforms(rows)
        return [
            _jd_dto(row, source_platform=platforms.get(row.source_jd_id or ""))
            for row in rows
        ]

    def owned_enterprise_ids(self, user_id: str) -> list[str]:
        return [
            enterprise_id
            for (enterprise_id,) in self._session.query(Enterprise.id)
            .filter(Enterprise.owner_user_id == user_id)
            .all()
        ]

    def create_jd(self, command: JDCreateCommand) -> JDDTO:
        jd = JobDescription(**_jd_create_values(command))
        self._session.add(jd)
        self._session.flush()
        self._session.refresh(jd)
        return _jd_dto(jd)

    def list_jds(self, enterprise_ids: list[str] | None = None) -> list[JDDTO]:
        query = self._session.query(JobDescription).filter(
            JobDescription.is_deprecated.is_(False)
        )
        if enterprise_ids is not None:
            query = query.filter(JobDescription.enterprise_id.in_(enterprise_ids))
        rows = query.order_by(JobDescription.created_at.desc()).all()
        return self._jd_dtos(rows)

    def list_jds_page(
        self,
        enterprise_ids: list[str] | None,
        *,
        offset: int,
        limit: int,
        query: str | None,
        sort: str = "created_desc",
    ) -> tuple[list[JDDTO], int]:
        statement = self._session.query(JobDescription).filter(
            JobDescription.is_deprecated.is_(False)
        )
        if enterprise_ids is not None:
            statement = statement.filter(JobDescription.enterprise_id.in_(enterprise_ids))
        needle = (query or "").strip()
        if needle:
            pattern = f"%{needle}%"
            statement = statement.filter(
                or_(
                    JobDescription.title.ilike(pattern),
                    JobDescription.source_name.ilike(pattern),
                    JobDescription.id.ilike(pattern),
                )
            )
        total = statement.count()
        if sort == "title_asc":
            order = JobDescription.title.asc()
        elif sort == "created_asc":
            order = JobDescription.created_at.asc()
        else:
            order = JobDescription.created_at.desc()
        rows = (
            statement.order_by(order)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return self._jd_dtos(rows), total

    def summarize_jds(self, enterprise_ids: list[str] | None) -> JDSummaryDTO:
        statement = (
            self._session.query(
                func.count(JobDescription.id),
                func.sum(case((JDParseResult.workflow_status == "draft", 1), else_=0)),
                func.sum(case((JDParseResult.workflow_status == "reviewed", 1), else_=0)),
                func.sum(case((JDParseResult.workflow_status == "published", 1), else_=0)),
                func.sum(case((JobDescription.parse_status == "failed", 1), else_=0)),
            )
            .outerjoin(JDParseResult, JDParseResult.jd_id == JobDescription.id)
        )
        if enterprise_ids is not None:
            statement = statement.filter(JobDescription.enterprise_id.in_(enterprise_ids))
        total, awaiting_review, reviewed, published, failed = statement.one()
        return JDSummaryDTO(
            total=int(total or 0),
            awaiting_review=int(awaiting_review or 0),
            reviewed=int(reviewed or 0),
            published=int(published or 0),
            failed=int(failed or 0),
        )

    def get_jd(self, jd_id: str) -> JDDTO | None:
        jd = self._session.query(JobDescription).filter(JobDescription.id == jd_id).first()
        return self._jd_dtos([jd])[0] if jd is not None else None

    def update_jd(self, jd_id: str, command: JDUpdateCommand) -> JDDTO:
        values = _jd_update_values(command)
        jd = self._require_jd(jd_id)
        for key, value in values.items():
            setattr(jd, key, value)
        self._session.flush()
        self._session.refresh(jd)
        return _jd_dto(jd)

    def get_parse_result(self, jd_id: str) -> JDParseResultDTO | None:
        result = (
            self._session.query(JDParseResult)
            .filter(JDParseResult.jd_id == jd_id)
            .first()
        )
        return _parse_dto(result) if result is not None else None

    def list_parse_results(self, jd_ids: list[str]) -> list[JDParseResultDTO]:
        if not jd_ids:
            return []
        results = (
            self._session.query(JDParseResult)
            .filter(JDParseResult.jd_id.in_(jd_ids))
            .all()
        )
        return [_parse_dto(result) for result in results]

    def get_parse_result_by_id(
        self, parse_result_id: str
    ) -> JDParseResultDTO | None:
        result = self._session.get(JDParseResult, parse_result_id)
        return _parse_dto(result) if result is not None else None

    def create_parse_result(self, command: JDParseResultCreateCommand) -> JDParseResultDTO:
        result = JDParseResult(**_parse_create_values(command))
        self._session.add(result)
        self._session.flush()
        self._session.refresh(result)
        return _parse_dto(result)

    def update_parse_result(
        self, parse_result_id: str, command: JDParseResultUpdateCommand
    ) -> JDParseResultDTO:
        values = _parse_update_values(command)
        if values.get("workflow_status") == "reviewed":
            raise ValueError("ReviewTask approval is the only reviewed-state authority")
        result = self._require_parse_result_by_id(parse_result_id)
        for key, value in values.items():
            setattr(result, key, value)
        self._session.flush()
        self._session.refresh(result)
        return _parse_dto(result)

    def upsert_parse_result(
        self, jd_id: str, command: JDParseResultCreateCommand
    ) -> JDParseResultDTO:
        values = _parse_create_values(command)
        result = (
            self._session.query(JDParseResult)
            .filter(JDParseResult.jd_id == jd_id)
            .first()
        )
        if result is None:
            return self.create_parse_result(command)
        for key, value in values.items():
            setattr(result, key, value)
        self._session.flush()
        self._session.refresh(result)
        return _parse_dto(result)

    def all_other_jds(self, jd_id: str) -> list[JDDTO]:
        return [
            _jd_dto(jd)
            for jd in self._session.query(JobDescription)
            .filter(JobDescription.id != jd_id)
            .all()
        ]

    def delete_jd(self, jd_id: str) -> None:
        result = (
            self._session.query(JDParseResult)
            .filter(JDParseResult.jd_id == jd_id)
            .first()
        )
        if result is not None:
            self._session.delete(result)
        self._session.delete(self._require_jd(jd_id))
        self._session.flush()

    def deprecate_jd(self, jd_id: str) -> None:
        jd = self._require_jd(jd_id)
        jd.is_deprecated = True
        result = (
            self._session.query(JDParseResult)
            .filter(JDParseResult.jd_id == jd_id)
            .first()
        )
        if result is not None:
            result.workflow_status = "deprecated"
            result.need_review = False
            active_tasks = (
                self._session.query(ReviewTask)
                .filter(
                    ReviewTask.object_type == "jd_parse_result",
                    ReviewTask.object_id == result.id,
                    ReviewTask.status.in_(("pending", "claimed", "modified")),
                )
                .all()
            )
            for task in active_tasks:
                self._session.query(ReviewTaskEvent).filter(
                    ReviewTaskEvent.task_id == task.id
                ).delete()
                self._session.delete(task)
        self._session.flush()

    def update_cleaned_text(self, jd_id: str, cleaned_text: str) -> None:
        jd = self._require_jd(jd_id)
        jd.cleaned_text = cleaned_text
        self._session.flush()

    def flush(self) -> None:
        self._session.flush()

    # Legacy entity flushing remains for callers that have not yet moved to the UoW.
    def save(self, *entities: JobDescription | JDParseResult) -> None:
        self._session.flush()

    def _require_jd(self, jd_id: str) -> JobDescription:
        jd = self._session.query(JobDescription).filter(JobDescription.id == jd_id).first()
        if jd is None:
            raise LookupError("JD not found")
        return jd

    def _require_parse_result_by_id(self, parse_result_id: str) -> JDParseResult:
        result = (
            self._session.query(JDParseResult)
            .filter(JDParseResult.id == parse_result_id)
            .first()
        )
        if result is None:
            raise LookupError("JD parse result not found")
        return result


class SqlAlchemyJDPublicationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _record(
        self, row: JDPublication, outbox: OutboxMessage | None = None
    ) -> JDPublicationDTO:
        outbox = outbox or (
            self._session.query(OutboxMessage)
            .filter(OutboxMessage.idempotency_key == row.idempotency_key)
            .one()
        )
        return JDPublicationDTO(
            id=row.id,
            parse_result_id=row.parse_result_id,
            jd_id=row.jd_id,
            source_jd_id=row.source_jd_id,
            source_jd_version_id=row.source_jd_version_id,
            extraction_task_id=row.extraction_task_id,
            document_id=row.document_id,
            schema_version=row.schema_version,
            normalization_schema_version=row.normalization_schema_version,
            idempotency_key=row.idempotency_key,
            snapshot_payload=_json_object(
                row.snapshot_payload, field="snapshot_payload"
            ),
            outbox_event_id=outbox.event_id,
            outbox_status=outbox.status,
            published_by=row.published_by,
            created_at=row.created_at,
        )

    def get_by_parse_result(
        self, parse_result_id: str
    ) -> JDPublicationDTO | None:
        row = (
            self._session.query(JDPublication)
            .filter(JDPublication.parse_result_id == parse_result_id)
            .one_or_none()
        )
        return self._record(row) if row is not None else None

    def add(
        self,
        parse_result_id: str,
        *,
        published_by: str,
        published_by_role: str,
        validation_lineage: JsonObject,
    ) -> JDPublicationDTO:
        existing = (
            self._session.query(JDPublication)
            .filter(JDPublication.parse_result_id == parse_result_id)
            .one_or_none()
        )
        if existing is not None:
            return self._record(existing)
        parsed = self._session.get(JDParseResult, parse_result_id)
        if parsed is None:
            raise LookupError("JD parse result not found")
        jd = self._session.get(JobDescription, parsed.jd_id)
        if jd is None:
            raise LookupError("JD not found")
        if parsed.workflow_status != "published":
            raise ValueError("JD parse result must be published before snapshot creation")
        version = (
            self._session.get(SourceJDVersion, jd.source_jd_version_id)
            if jd.source_jd_version_id
            else None
        )
        if version is not None:
            source_version = version.source_version
            source_content_hash = _content_hash(
                {
                    "source_version": version.source_version,
                    "raw_text": version.raw_text,
                    "cleaned_text": jd.cleaned_text or "",
                }
            )
        else:
            source_content_hash = _content_hash(
                {"raw_text": jd.raw_text, "cleaned_text": jd.cleaned_text or ""}
            )
            source_version = "manual:" + source_content_hash
        document_id = jd.source_document_id or jd.id
        normalized_projection = _publication_normalization_projection(
            parsed.normalized_result
        )
        eligible_skill_ids = {
            str(item["skill_id"])
            for item in normalized_projection["normalized_requirements"]
            if isinstance(item, dict)
        }
        content_basis = {
            "parse_result_id": parsed.id,
            "jd_id": jd.id,
            "source_jd_id": jd.source_jd_id,
            "source_jd_version_id": jd.source_jd_version_id,
            "extraction_task_id": jd.extraction_task_id,
            "document_id": document_id,
            "source_version": source_version,
            "source_content_hash": source_content_hash,
            "schema_version": parsed.schema_version,
            "normalization_schema_version": parsed.normalization_schema_version,
            "extraction_result": parsed.extraction_result,
            "normalized_result": normalized_projection,
            "legacy": {
                "position_title": parsed.position_title,
                "responsibilities": parsed.responsibilities,
                "required_skills": _eligible_legacy_skills(
                    parsed.required_skills,
                    field="required_skills",
                    eligible_skill_ids=eligible_skill_ids,
                ),
                "bonus_skills": _eligible_legacy_skills(
                    parsed.bonus_skills,
                    field="bonus_skills",
                    eligible_skill_ids=eligible_skill_ids,
                ),
                "education": parsed.education,
                "experience": parsed.experience,
                "industry": parsed.industry,
                "tools": parsed.tools,
                "business_scenarios": parsed.business_scenarios,
            },
        }
        created_at = utc_now()
        frozen_skill, frozen_position = _parse_result_catalog_identity(parsed)
        if frozen_skill is not None and frozen_position is not None:
            skill_catalog_snapshot = {
                "source": "main-system-skill-catalog",
                **frozen_skill,
                "effective_at": created_at.isoformat(),
                "status": "active",
            }
            position_catalog_snapshot = {
                "source": "main-system-position-catalog",
                **frozen_position,
                "effective_at": created_at.isoformat(),
                "status": "active",
            }
        else:
            skill_catalog_snapshot = _catalog_snapshot(self._session, created_at)
            position_catalog_snapshot = _position_catalog_snapshot(
                self._session, created_at
            )
        snapshot_basis = {
            "contract_version": "jd-publication-snapshot.v3",
            "validation_lineage": dict(validation_lineage),
            "skill_catalog_snapshot": skill_catalog_snapshot,
            "position_catalog_snapshot": position_catalog_snapshot,
            **content_basis,
        }
        idempotency_key = publication_idempotency_key(snapshot_basis)
        publication_id = str(uuid4())
        snapshot_payload = {
            **snapshot_basis,
            "publication_id": publication_id,
            "published_at": created_at.isoformat(),
            "published_by": published_by,
            "published_by_role": published_by_role,
            "jd": {
                "title": jd.title,
                "raw_text": jd.raw_text,
                "source_type": jd.source_type,
                "source_name": jd.source_name,
                "enterprise_id": jd.enterprise_id,
                "publish_date": jd.publish_date.isoformat() if jd.publish_date else None,
                "url": jd.url,
            },
        }
        row = JDPublication(
            id=publication_id,
            parse_result_id=parsed.id,
            jd_id=jd.id,
            source_jd_id=jd.source_jd_id,
            source_jd_version_id=jd.source_jd_version_id,
            extraction_task_id=jd.extraction_task_id,
            document_id=document_id,
            schema_version=parsed.schema_version,
            normalization_schema_version=parsed.normalization_schema_version,
            idempotency_key=idempotency_key,
            snapshot_payload=snapshot_payload,
            published_by=published_by,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        event_id = str(uuid4())
        event_type = "jd.publication.created"
        event_payload = freeze_json_object(
            {
                "event_id": event_id,
                "event_type": event_type,
                "aggregate_id": jd.id,
                "parse_result_id": parsed.id,
                "published_fact_id": publication_id,
                "publication_id": publication_id,
                "source_jd_id": jd.source_jd_id,
                "source_jd_version_id": jd.source_jd_version_id,
                "extraction_task_id": jd.extraction_task_id,
                "document_id": document_id,
                "schema_version": parsed.schema_version,
                "normalization_schema_version": (
                    parsed.normalization_schema_version
                ),
                "source_version": source_version,
                "source_content_hash": source_content_hash,
                "created_at": created_at.isoformat(),
                "published_by": published_by,
                "published_by_role": published_by_role,
            }
        )
        outbox_record = SqlAlchemyOutboxRepository(self._session).add(
            OutboxMessageDraft(
                IntegrationEvent(
                    event_id=event_id,
                    event_type=event_type,
                    aggregate_id=jd.id,
                    payload=event_payload,
                    occurred_at=created_at,
                ),
                IdempotencyKey(idempotency_key),
            )
        )
        outbox = self._session.get(OutboxMessage, outbox_record.message_id)
        return self._record(row, outbox)


class SqlAlchemyFileRepository(FileRepository):
    def __init__(self, session: Session) -> None:
        self._session = session
        self._saved_storage_keys: list[str] = []

    def save_upload(
        self, actor: Actor, upload: FileUpload, *, purpose: str
    ) -> FileAssetDTO:
        original_name = Path(upload.filename or "").name
        suffix = Path(original_name).suffix.lower()
        allowed_suffixes = ALLOWED_UPLOAD_TYPES.get(upload.content_type or "")
        if not original_name or not allowed_suffixes or suffix not in allowed_suffixes:
            raise ValueError("Unsupported file type")
        if not upload.content:
            raise ValueError("File is empty")
        if len(upload.content) > settings.MAX_UPLOAD_SIZE_BYTES:
            raise ValueError("File exceeds configured size limit")

        stored_name = f"{uuid4()}{suffix}"
        storage = get_integration_registry().file_storage
        storage_key = storage.save(stored_name, upload.content)
        self._saved_storage_keys.append(storage_key)
        file_asset = FileAsset(
            owner_user_id=actor.id,
            filename=original_name,
            content_type=upload.content_type,
            path=storage_key,
            size=len(upload.content),
            purpose=purpose,
        )
        self._session.add(file_asset)
        self._session.flush()
        self._session.refresh(file_asset)
        return _file_dto(file_asset)

    def cleanup_uncommitted_files(self) -> None:
        storage = get_integration_registry().file_storage
        for storage_key in self._saved_storage_keys:
            storage.delete(storage_key)
        self._saved_storage_keys.clear()

    def mark_committed(self) -> None:
        self._saved_storage_keys.clear()


class AdapterFileTextExtractor(FileTextExtractor):
    def __init__(self, session: Session) -> None:
        self._session = session

    def extract_text(
        self, file_asset: FileAssetDTO, *, use_ocr: bool
    ) -> FileTextExtractionResult:
        if not isinstance(file_asset, FileAssetDTO):
            raise TypeError("file_asset must be FileAssetDTO")
        orm_file = self._session.query(FileAsset).filter(FileAsset.id == file_asset.id).first()
        if orm_file is None:
            raise LookupError("File not found")
        outcome = extract_file_text(orm_file, use_ocr=use_ocr)
        return FileTextExtractionResult(
            outcome.status,
            outcome.text,
            outcome.provider,
            outcome.error_code,
            outcome.error_message,
        )


class SqlAlchemyTaskRepository(TaskRepository):
    """JD's minimal task adapter; transaction ownership stays with SqlAlchemyJDUoW."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_task(self, task_id: str) -> TaskDTO | None:
        task = self._session.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        return _task_dto(task) if task is not None else None

    def create_succeeded_task(
        self,
        actor: Actor,
        task_type: str,
        *,
        input_payload: JsonObject,
        result_payload: JsonObject,
        result_reference: str | None,
        task_id: str | None = None,
        execution_metadata: JsonObject | None = None,
    ) -> TaskDTO:
        now = datetime.now(timezone.utc)
        typed_input = _json_persistence_object(input_payload, field="input_payload")
        typed_result = _json_persistence_object(result_payload, field="result_payload")
        truthful_result: MutableJsonObject = {
            **(execution_metadata or {}),
            **typed_result,
        }
        task = TaskRecord(
            id=task_id or f"{task_type}_{uuid4()}",
            task_type=task_type,
            status="succeeded",
            progress=1.0,
            input_payload=typed_input,
            result_payload=truthful_result,
            result_reference=result_reference,
            created_by=actor.id,
            log_entries=[
                {"status": "pending", "at": now.isoformat()},
                {"status": "running", "at": now.isoformat(), "message": None},
                {
                    "status": "succeeded",
                    "at": now.isoformat(),
                    "message": "Completed by synchronous local executor",
                },
            ],
            started_at=now,
            finished_at=now,
        )
        self._session.add(task)
        self._session.flush()
        self._session.refresh(task)
        return _task_dto(task)

    def create_succeeded_parse_task(
        self,
        actor: Actor,
        command: JDParseCommand,
        result: JDParseResultDTO,
        schema_view: JDSchemaView,
    ) -> TaskDTO:
        if command.extraction_mode == "rule":
            execution_metadata: JsonObject = {
                "execution_mode": "rule",
                "implementation_status": "deterministic_rule_jd_parse",
                "provider": "local_rules",
                "rule_based": True,
                "review_only": True,
                "algorithm_version": "jd-rule-v1",
            }
        else:
            execution_metadata = {
                "execution_mode": "llm",
                "implementation_status": "llm_jd_parse",
                "provider": command.model or "external_llm",
                "rule_based": False,
            }
        return self.create_succeeded_task(
            actor,
            "jd_parse",
            input_payload={
                "jd_id": result.jd_id,
                "extraction_mode": command.extraction_mode,
                "model": command.model,
                "use_skill_dictionary": command.use_skill_dictionary,
                "auto_normalize_skill": command.auto_normalize_skill,
            },
            result_payload={
                "jd_id": result.jd_id,
                "parse_result": parse_result_payload(result, schema_view),
            },
            result_reference=f"jd_parse_result:{result.id}",
            execution_metadata=execution_metadata,
        )


class SqlAlchemyJDUoW(JDUoW):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        close_session: bool = True,
        data_validation_mode: str = "off",
    ):
        self._session_factory = session_factory
        self._close_session = close_session
        self._data_validation_mode = data_validation_mode
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemyJDUoW":
        self.session = self._session_factory()
        self.jds = SqlAlchemyJDRepository(self.session)
        self.publications = SqlAlchemyJDPublicationRepository(self.session)
        self.files = SqlAlchemyFileRepository(self.session)
        self.file_text_extractor = AdapterFileTextExtractor(self.session)
        self.tasks = SqlAlchemyTaskRepository(self.session)
        from app.infrastructure.governance import SqlAlchemyReviewRepository

        self.review_tasks = SqlAlchemyReviewRepository(self.session)
        from app.infrastructure.skills import SqlAlchemySkillRepository

        self.skills = SqlAlchemySkillRepository(self.session)
        if self._data_validation_mode == "enforce":
            from app.infrastructure.data_validation import (
                SqlAlchemyValidationPublicationGate,
            )

            self.validation_publication_gate = (
                SqlAlchemyValidationPublicationGate(self.session)
            )
        return self

    def catalog_entries(
        self,
    ) -> tuple[tuple[CatalogSkill, ...], tuple[CatalogAlias, ...]]:
        from app.infrastructure.data_validation import load_catalog_entries

        return load_catalog_entries(self.session)

    def catalog_identity(self) -> CatalogIdentity:
        from app.infrastructure.data_validation import frozen_catalog_identity

        return CatalogIdentity(**frozen_catalog_identity(self.session))

    def position_catalog_identity(self) -> CatalogIdentity:
        from app.infrastructure.data_validation import (
            frozen_position_catalog_identity,
        )

        return CatalogIdentity(**frozen_position_catalog_identity(self.session))

    def position_catalog_entry(
        self, position_id: str
    ) -> tuple[
        str,
        str,
        str,
        str | None,
        str | None,
        tuple[str, ...],
        str,
        str,
    ] | None:
        if self.session is None:
            raise RuntimeError("UoW is not active")
        row = self.session.get(StandardPosition, position_id)
        if row is None:
            return None
        return (
            str(row.id),
            str(row.position_code),
            str(row.position_name),
            str(row.taxonomy_family_code) if row.taxonomy_family_code else None,
            str(row.taxonomy_family_name) if row.taxonomy_family_name else None,
            tuple(str(code) for code in (row.skill_domain_codes or [])),
            str(row.taxonomy_version),
            str(row.lifecycle_status),
        )

    def stage_validation_for_parse_result(self, parse_result_id: str) -> None:
        if self._data_validation_mode != "enforce":
            return
        if self.session is None:
            raise RuntimeError("UoW is not active")
        from app.infrastructure.data_validation import SqlAlchemyValidationTaskScheduler
        from app.infrastructure.jd_validation_projection import (
            build_reviewed_extraction_bundle,
        )
        from app.models.data_validation import DataValidationTask, ValidationReport
        from app.models.review_task import ReviewTask

        parsed = self.session.get(JDParseResult, parse_result_id)
        if parsed is None:
            raise LookupError(parse_result_id)
        jd = self.session.get(JobDescription, parsed.jd_id)
        if jd is None or parsed.extraction_result is None or parsed.normalized_result is None:
            raise ValueError("Versioned JD parse result is required for validation")

        source_platform = str(jd.source_type).strip().lower()
        source_record_id = str(jd.source_name or "").removeprefix("batch:").strip()
        if not source_record_id:
            source_record_id = str(parsed.extraction_result.get("document_id") or jd.id)
        source_version = "review:" + _content_hash(
            {"raw_text": jd.raw_text, "cleaned_text": jd.cleaned_text or ""}
        )
        # The staged bundle must stay byte-identical to the one the
        # publication gate rebuilds later, so derive the run timestamp from
        # the immutable created_at: updated_at moves whenever the parse
        # result row is touched (e.g. review approval flips workflow_status).
        timestamp = parsed.created_at or utc_now()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        provider = "reviewed-jd-projection"
        bundle = build_reviewed_extraction_bundle(
            source_platform=source_platform,
            source_record_id=source_record_id,
            source_version=source_version,
            cleaned_text=jd.cleaned_text,
            extraction_result=parsed.extraction_result,
            normalized_result=parsed.normalized_result,
            provider=provider,
            model_version=str(parsed.schema_version),
            run_id=f"review:{parsed.id}:{timestamp.isoformat()}",
            timestamp=timestamp,
        )
        bundle_payload = bundle.model_dump(mode="json")
        source = self.session.query(SourceJD).filter(
            SourceJD.source_platform == source_platform,
            SourceJD.source_record_id == source_record_id,
        ).one_or_none()
        if source is None:
            source = SourceJD(
                source_platform=source_platform,
                source_record_id=source_record_id,
            )
            self.session.add(source)
            self.session.flush()
        version = self.session.query(SourceJDVersion).filter(
            SourceJDVersion.source_jd_id == source.id,
            SourceJDVersion.source_version == source_version,
        ).one_or_none()
        if version is None:
            version = SourceJDVersion(
                source_jd_id=source.id,
                source_version=source_version,
                schema_version="crawler-jd-envelope-v1",
                raw_text=jd.raw_text,
                content_hash=compute_content_hash(jd.raw_text),
                raw_payload={
                    "source_platform": source_platform,
                    "source_record_id": source_record_id,
                    "title": jd.title,
                    "source_name": jd.source_name,
                },
                source_url=jd.url,
                crawl_time=timestamp,
                job_title_raw=jd.title,
                publish_time_raw=jd.publish_date.isoformat() if jd.publish_date else None,
                text_canonicalization_version="identity-v1",
            )
            self.session.add(version)
            self.session.flush()
            source.latest_version_id = version.id
            self.session.flush()

        # extraction_tasks.request_id is capped at 128 chars; the full
        # source_version hash is already persisted on SourceJDVersion.
        request_id = (
            f"reviewed-jd-projection:{parse_result_id}:{source_version[:40]}"
        )
        task = self.session.query(ExtractionTask).filter(
            ExtractionTask.source_jd_version_id == version.id,
            ExtractionTask.request_id == request_id,
        ).one_or_none()
        if task is None:
            task = ExtractionTask(
                source_jd_version_id=version.id,
                status="succeeded",
                extraction_mode="rule",
                provider=provider,
                request_id=request_id,
                attempt_count=1,
                max_attempts=1,
                started_at=timestamp,
                finished_at=timestamp,
                retryable=False,
                bundle_payload=bundle_payload,
            )
            self.session.add(task)
            self.session.flush()
        obsolete_report_ids = (
            self.session.query(ValidationReport.id)
            .join(
                DataValidationTask,
                ValidationReport.data_validation_task_id == DataValidationTask.id,
            )
            .filter(
                DataValidationTask.source_jd_version_id == version.id,
                DataValidationTask.extraction_task_id != task.id,
            )
        )
        self.session.query(ReviewTask).filter(
            ReviewTask.object_type == "data_validation_report",
            ReviewTask.object_id.in_(obsolete_report_ids),
            ReviewTask.status.in_(("pending", "claimed")),
        ).update(
            {
                ReviewTask.status: "modified",
                ReviewTask.review_comment: "已由该 JD 的最新人工确认版本替代。",
                ReviewTask.updated_at: utc_now(),
            },
            synchronize_session=False,
        )
        jd.source_jd_id = source.id
        jd.source_jd_version_id = version.id
        jd.extraction_task_id = task.id
        jd.source_document_id = bundle.extraction_result.document_id
        jd.extraction_bundle_version = bundle.schema_version
        jd.input_provider = provider
        SqlAlchemyValidationTaskScheduler(self.session).ensure_for_extraction(
            extraction_task_id=task.id,
            source_jd_version_id=version.id,
            bundle_payload=bundle_payload,
        )
        self.session.flush()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        if self.session is not None and self._close_session:
            self.session.close()

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("UoW is not active")
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.rollback()
            raise
        self.files.mark_committed()

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()
        if self.session is not None:
            self.files.cleanup_uncommitted_files()
