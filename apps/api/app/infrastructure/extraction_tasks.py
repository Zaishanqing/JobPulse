from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

import httpx
from pydantic import ValidationError
from sqlalchemy import and_, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.extraction_tasks import (
    ExtractionProviderError,
    ExtractionTaskConflict,
    ExtractionTaskRecord,
)
from app.contexts.extraction_tasks.ports import (
    ExtractionDraftCreate,
    ExtractionDraftRecord,
    RawJDDraftCreate,
)
from app.contexts.source_jds.ports import SourceJDRecord, SourceJDVersionRecord
from app.domain.jd_skill_catalog import CatalogIdentity
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.infrastructure.source_jds import SqlAlchemySourceJDRepository
from app.infrastructure.skills import SqlAlchemySkillRepository
from app.models.extraction_task import ExtractionTask
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.review_task import ReviewTask
from app.models.review_task_event import ReviewTaskEvent
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_bundle import (
    ExtractedJDBundleV2,
    JDExtractionExecutionV2,
    parse_extracted_jd_bundle,
)
from jobgraph_contracts.normalization_v2 import JDNormalizedResult
from jobgraph_contracts.extraction_v2 import JDExtractionResult
from jobgraph_contracts.skill_taxonomy import (
    SkillClassificationSetV1,
    SkillTaxonomyProjectionV1,
)
from app.infrastructure.jd_extraction_mapper import domain_to_extraction
from app.infrastructure.jd_normalization_mapper import domain_to_normalization
from app.infrastructure.jd_schema import VersionedJDSchemaAdapter


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _task_record(row: ExtractionTask) -> ExtractionTaskRecord:
    payload = (
        freeze_json_object(row.bundle_payload, field="bundle_payload")
        if row.bundle_payload is not None
        else None
    )
    return ExtractionTaskRecord(
        id=row.id,
        source_jd_version_id=row.source_jd_version_id,
        status=row.status,
        extraction_mode=row.extraction_mode,
        provider=row.provider,
        request_id=row.request_id,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        retryable=row.retryable,
        bundle_payload=payload,
        claimed_by=row.claimed_by,
        lease_expires_at=_aware(row.lease_expires_at),
        heartbeat_at=_aware(row.heartbeat_at),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


class SqlAlchemyExtractionTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, task_id: str, *, for_update: bool = False) -> ExtractionTaskRecord | None:
        query = self._session.query(ExtractionTask).filter(ExtractionTask.id == task_id)
        if for_update:
            query = query.with_for_update()
        row = query.first()
        return _task_record(row) if row is not None else None

    def get_by_idempotency_key(
        self, source_jd_version_id: str, request_id: str
    ) -> ExtractionTaskRecord | None:
        row = (
            self._session.query(ExtractionTask)
            .filter(
                ExtractionTask.source_jd_version_id == source_jd_version_id,
                ExtractionTask.request_id == request_id,
            )
            .first()
        )
        return _task_record(row) if row is not None else None

    def add(
        self,
        source_jd_version_id: str,
        extraction_mode: str,
        provider: str,
        request_id: str,
        max_attempts: int,
    ) -> ExtractionTaskRecord:
        row = ExtractionTask(
            source_jd_version_id=source_jd_version_id,
            extraction_mode=extraction_mode,
            provider=provider,
            request_id=request_id,
            max_attempts=max_attempts,
        )
        self._session.add(row)
        self._flush()
        return _task_record(row)

    def mark_running(self, task_id: str, started_at) -> ExtractionTaskRecord:
        row = self._require(task_id)
        row.status = "running"
        row.attempt_count += 1
        row.started_at = started_at
        row.finished_at = None
        row.last_error_code = None
        row.last_error_message = None
        row.retryable = False
        row.heartbeat_at = started_at
        self._flush()
        return _task_record(row)

    def mark_succeeded(self, task_id: str, bundle_payload, finished_at) -> ExtractionTaskRecord:
        row = self._require(task_id)
        row.status = "succeeded"
        row.bundle_payload = thaw_json_object(bundle_payload)
        row.finished_at = finished_at
        row.last_error_code = None
        row.last_error_message = None
        row.retryable = False
        row.claimed_by = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        self._flush()
        return _task_record(row)

    def mark_failed(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        finished_at,
    ) -> ExtractionTaskRecord:
        row = self._require(task_id)
        row.status = "failed"
        row.bundle_payload = None
        row.finished_at = finished_at
        row.last_error_code = error_code
        row.last_error_message = error_message
        row.retryable = retryable
        row.claimed_by = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        self._flush()
        return _task_record(row)

    def list(
        self,
        *,
        status: str | None,
        source_jd_version_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[tuple[ExtractionTaskRecord, ...], int]:
        query = self._session.query(ExtractionTask)
        if status is not None:
            query = query.filter(ExtractionTask.status == status)
        if source_jd_version_id is not None:
            query = query.filter(ExtractionTask.source_jd_version_id == source_jd_version_id)
        total = query.count()
        rows = (
            query.order_by(ExtractionTask.created_at.desc(), ExtractionTask.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return tuple(_task_record(row) for row in rows), total

    def claim_next(self, worker_id: str, now, lease_expires_at) -> ExtractionTaskRecord | None:
        eligible_status = or_(
            ExtractionTask.status == "pending",
            and_(
                ExtractionTask.status == "failed",
                ExtractionTask.retryable.is_(True),
                ExtractionTask.attempt_count < ExtractionTask.max_attempts,
            ),
        )
        lease_available = or_(
            ExtractionTask.claimed_by.is_(None),
            ExtractionTask.lease_expires_at.is_(None),
            ExtractionTask.lease_expires_at <= now,
        )
        query = (
            self._session.query(ExtractionTask)
            .filter(eligible_status, lease_available)
            .order_by(ExtractionTask.created_at, ExtractionTask.id)
        )
        if self._session.get_bind().dialect.name != "sqlite":
            query = query.with_for_update(skip_locked=True)
        row = query.first()
        if row is None:
            return None
        row.claimed_by = worker_id
        row.lease_expires_at = lease_expires_at
        row.heartbeat_at = now
        self._flush()
        return _task_record(row)

    def renew_lease(self, task_id: str, worker_id: str, now, lease_expires_at) -> bool:
        row = self._session.get(ExtractionTask, task_id)
        if row is None or row.claimed_by != worker_id or row.status not in {"pending", "running"}:
            return False
        row.heartbeat_at = now
        row.lease_expires_at = lease_expires_at
        self._flush()
        return True

    def recover_stale(self, now) -> int:
        rows = (
            self._session.query(ExtractionTask)
            .filter(
                ExtractionTask.claimed_by.is_not(None),
                ExtractionTask.lease_expires_at.is_not(None),
                ExtractionTask.lease_expires_at <= now,
                ExtractionTask.status.in_(("pending", "running")),
            )
            .all()
        )
        for row in rows:
            if row.status == "running":
                row.status = "failed"
                row.finished_at = now
                row.last_error_code = "extraction_lease_expired"
                row.last_error_message = "Extraction worker lease expired."
                row.retryable = row.attempt_count < row.max_attempts
                row.bundle_payload = None
            row.claimed_by = None
            row.lease_expires_at = None
            row.heartbeat_at = None
        if rows:
            self._flush()
        return len(rows)

    def _require(self, task_id: str) -> ExtractionTask:
        row = self._session.get(ExtractionTask, task_id)
        if row is None:
            raise LookupError(task_id)
        return row

    def _flush(self) -> None:
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ExtractionTaskConflict("ExtractionTask persistence conflicted") from exc


class SqlAlchemyExtractionSourceRepository:
    def __init__(self, session: Session) -> None:
        self._delegate = SqlAlchemySourceJDRepository(session)

    def get_source(self, source_jd_id: str) -> SourceJDRecord | None:
        return self._delegate.get(source_jd_id)

    def get_version(self, version_id: str) -> SourceJDVersionRecord | None:
        return self._delegate.get_version(version_id)


class SqlAlchemyExtractionDraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(
        jd: JobDescription, result: JDParseResult, review: ReviewTask
    ) -> ExtractionDraftRecord:
        return ExtractionDraftRecord(
            jd_id=jd.id,
            parse_result_id=result.id,
            review_task_id=review.id,
            source_jd_id=jd.source_jd_id,
            source_jd_version_id=jd.source_jd_version_id,
            extraction_task_id=jd.extraction_task_id,
            document_id=jd.source_document_id,
            bundle_id=jd.extraction_bundle_version,
            workflow_status=result.workflow_status,
            need_review=result.need_review,
            created_at=_aware(jd.created_at),
        )

    def _query(self):
        return (
            self._session.query(JobDescription, JDParseResult, ReviewTask)
            .join(JDParseResult, JDParseResult.jd_id == JobDescription.id)
            .join(
                ReviewTask,
                and_(
                    ReviewTask.object_type == "jd_parse_result",
                    ReviewTask.object_id == JDParseResult.id,
                ),
            )
        )

    def get_by_task(self, task_id: str) -> ExtractionDraftRecord | None:
        row = self._query().filter(JobDescription.extraction_task_id == task_id).first()
        return self._record(*row) if row is not None else None

    def raw_exists(self, source_jd_version_id: str) -> bool:
        row = (
            self._session.query(JobDescription.id)
            .filter(JobDescription.source_jd_version_id == source_jd_version_id)
            .first()
        )
        return row is not None

    def list_by_source_version(
        self, source_jd_version_id: str
    ) -> tuple[ExtractionDraftRecord, ...]:
        rows = (
            self._query()
            .filter(JobDescription.source_jd_version_id == source_jd_version_id)
            .order_by(JobDescription.created_at.desc(), JobDescription.id.desc())
            .all()
        )
        return tuple(self._record(*row) for row in rows)

    def add_raw(self, draft: RawJDDraftCreate) -> str:
        jd = JobDescription(
            id=draft.jd_id,
            source_type="crawler_bundle",
            source_name=draft.source_name or draft.source_platform,
            title=draft.title,
            raw_text=draft.raw_text,
            cleaned_text=None,
            url=draft.source_url,
            parse_status="pending",
            input_extraction_status="not_required",
            input_provider="direct_text",
            source_jd_id=draft.source_jd_id,
            source_jd_version_id=draft.source_jd_version_id,
            extraction_task_id=None,
            source_document_id=None,
            extraction_bundle_version=None,
        )
        self._session.add(jd)
        self._session.flush()
        return jd.id

    def add(self, draft: ExtractionDraftCreate) -> ExtractionDraftRecord:
        jd = JobDescription(
            id=draft.jd_id,
            source_type="extraction_bundle",
            source_name=draft.source_name or draft.source_platform,
            title=draft.title,
            raw_text=draft.raw_text,
            cleaned_text=draft.cleaned_text,
            url=draft.source_url,
            parse_status="completed",
            input_extraction_status="completed",
            input_provider=draft.extraction_provider,
            source_jd_id=draft.source_jd_id,
            source_jd_version_id=draft.source_jd_version_id,
            extraction_task_id=draft.extraction_task_id,
            source_document_id=draft.document_id,
            extraction_bundle_version=draft.bundle_id,
        )
        result = JDParseResult(
            jd_id=draft.jd_id,
            position_title=draft.position_title,
            responsibilities=list(draft.responsibilities),
            required_skills=[thaw_json_object(item) for item in draft.required_skills],
            bonus_skills=[thaw_json_object(item) for item in draft.bonus_skills],
            education=draft.education,
            experience=draft.experience,
            industry=draft.industry,
            tools=list(draft.tools),
            business_scenarios=list(draft.business_scenarios),
            parse_confidence=0.0,
            need_review=True,
            extraction_result=thaw_json_object(draft.extraction_payload),
            normalized_result=thaw_json_object(draft.normalization_payload),
            execution_metadata=(
                thaw_json_object(draft.execution_metadata)
                if draft.execution_metadata is not None
                else None
            ),
            schema_version="v2",
            normalization_schema_version="v2",
            workflow_status="draft",
        )
        self._session.add_all((jd, result))
        self._session.flush()
        review = ReviewTask(
            object_type="jd_parse_result",
            object_id=result.id,
            priority="normal",
            reason="Extraction Bundle imported; human review required.",
            status="pending",
        )
        self._session.add(review)
        self._session.flush()
        self._session.add(
            ReviewTaskEvent(
                task_id=review.id,
                actor_user_id="system:extraction-import",
                action="create",
                before_status=None,
                after_status="pending",
                comment=review.reason,
                payload_snapshot={
                    "object_type": review.object_type,
                    "object_id": review.object_id,
                    "extraction_task_id": draft.extraction_task_id,
                },
            )
        )
        self._session.flush()
        return self._record(jd, result, review)


class SqlAlchemyExtractionTaskUnitOfWork:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        data_validation_mode: str = "off",
    ) -> None:
        self._session_factory = session_factory
        self._data_validation_mode = data_validation_mode
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyExtractionTaskUnitOfWork":
        self._session = self._session_factory()
        self.tasks = SqlAlchemyExtractionTaskRepository(self._session)
        self.sources = SqlAlchemyExtractionSourceRepository(self._session)
        self.source_jds = SqlAlchemySourceJDRepository(self._session)
        self.drafts = SqlAlchemyExtractionDraftRepository(self._session)
        self.skills = SqlAlchemySkillRepository(self._session)
        if self._data_validation_mode != "off":
            from app.infrastructure.data_validation import (
                SqlAlchemyValidationDraftGate,
                SqlAlchemyValidationTaskScheduler,
            )

            self.validation_tasks = SqlAlchemyValidationTaskScheduler(self._session)
            self.validation_gate = SqlAlchemyValidationDraftGate(self._session)
        return self

    def catalog_entries(self):
        from app.infrastructure.data_validation import load_catalog_entries

        return load_catalog_entries(self._session)

    def catalog_identity(self) -> CatalogIdentity:
        from app.infrastructure.data_validation import frozen_catalog_identity

        return CatalogIdentity(**frozen_catalog_identity(self._session))

    def position_catalog_identity(self) -> CatalogIdentity:
        from app.infrastructure.data_validation import (
            frozen_position_catalog_identity,
        )

        return CatalogIdentity(**frozen_position_catalog_identity(self._session))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def acquire_task_lock(self, lock_key: str) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        dialect = self._session.get_bind().dialect.name
        if dialect == "sqlite":
            self._session.execute(text("BEGIN IMMEDIATE"))
        elif dialect == "postgresql":
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )

    def acquire_orchestration_lock(self, lock_key: str) -> None:
        self.acquire_task_lock(lock_key)

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise ExtractionTaskConflict("ExtractionTask persistence conflicted") from exc

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()


UPSTREAM_ERROR_MAP = {
    "model_timeout": ("extraction_model_timeout", True),
    "model_unavailable": ("extraction_unavailable", True),
    "service_not_ready": ("extraction_unavailable", True),
    "internal_error": ("extraction_upstream_internal", True),
    "unauthorized": ("extraction_auth_failed", False),
    "invalid_envelope": ("extraction_request_rejected", False),
    "extraction_version_mismatch": ("extraction_request_rejected", False),
    "model_invalid_response": ("extraction_contract_rejected", False),
    "schema_validation_failed": ("extraction_contract_rejected", False),
    "evidence_validation_failed": ("extraction_contract_rejected", False),
    "semantic_validation_failed": ("extraction_contract_rejected", False),
    "business_validation_failed": ("extraction_contract_rejected", False),
    "normalization_failed": ("extraction_contract_rejected", False),
    "contract_validation_failed": ("extraction_contract_rejected", False),
}


class HttpJDExtractionProvider:
    def __init__(
        self,
        base_url: str | None,
        internal_token: str | None,
        connect_timeout: float,
        read_timeout: float,
        transport: httpx.BaseTransport | None = None,
        model_service_config: Callable[[], tuple[str, str, str] | None] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._token = internal_token
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self._transport = transport
        self._model_service_config = model_service_config
        self._request_id = "http_jd_extraction:v2"

    @property
    def name(self) -> str:
        return "http_jd_extraction"

    @property
    def mode(self) -> str:
        return "llm"

    @property
    def enabled(self) -> bool:
        return bool(self._base_url and self._token)

    @property
    def request_id(self) -> str:
        return self._request_id

    def extract(self, envelope: CrawlerJDEnvelopeV1) -> ExtractedJDBundleV2:
        if self._base_url is None or self._token is None:
            raise ExtractionProviderError(
                "extraction_provider_not_configured",
                "Extraction provider is not configured.",
                retryable=False,
            )
        model_config = self._model_service_config() if self._model_service_config else None
        if model_config is None:
            raise ExtractionProviderError(
                "extraction_provider_not_configured",
                "Model service configuration is not available.",
                retryable=False,
            )
        model_base_url, model_name, model_api_key = model_config
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = client.post(
                    "/api/v2/extractions",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "X-JobPulse-Model-Base-URL": model_base_url,
                        "X-JobPulse-Model-Name": model_name,
                        "X-JobPulse-Model-API-Key": model_api_key,
                    },
                    json=envelope.model_dump(mode="json"),
                )
        except httpx.TimeoutException as exc:
            raise ExtractionProviderError(
                "extraction_timeout", "Extraction service timed out.", retryable=True
            ) from exc
        except httpx.TransportError as exc:
            raise ExtractionProviderError(
                "extraction_unavailable",
                "Extraction service is unavailable.",
                retryable=True,
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExtractionProviderError(
                "extraction_invalid_response",
                "Extraction service returned an invalid response.",
                retryable=False,
            ) from exc
        if response.status_code >= 400:
            data = payload.get("data") if isinstance(payload, dict) else None
            upstream_code = data.get("error_code") if isinstance(data, dict) else None
            mapped_code, retryable = UPSTREAM_ERROR_MAP.get(
                upstream_code,
                ("extraction_upstream_error", response.status_code >= 500),
            )
            raise ExtractionProviderError(
                mapped_code,
                "Extraction service rejected the request.",
                retryable=retryable,
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        try:
            parsed = parse_extracted_jd_bundle(data)
            if not isinstance(parsed, ExtractedJDBundleV2):
                raise ValueError("Extraction provider did not return V2")
            return parsed
        except (ValidationError, ValueError, TypeError) as exc:
            raise ExtractionProviderError(
                "extraction_invalid_response",
                "Extraction service returned an invalid response.",
                retryable=False,
            ) from exc


class RuleBasedJDExtractionProvider:
    """Explicit, review-only adapter for the legacy deterministic JD rules."""

    def __init__(self, schema: VersionedJDSchemaAdapter | None = None) -> None:
        self._schema = schema or VersionedJDSchemaAdapter()
        catalog_path = (
            Path(__file__).resolve().parents[2] / "config" / "skill_taxonomy_catalog.v1.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        self._catalog_by_name = {
            str(item["canonical_name"]).casefold(): (skill_id, item)
            for skill_id, item in catalog["skills"].items()
        }
        self._request_id = "rule_based_jd_extraction:v1"

    @property
    def name(self) -> str:
        return "rule_based_jd_extraction"

    @property
    def mode(self) -> str:
        return "rule"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def request_id(self) -> str:
        return self._request_id

    def extract(self, envelope: CrawlerJDEnvelopeV1) -> ExtractedJDBundleV2:
        started_at = datetime.now(timezone.utc)
        document_id = (
            f"{envelope.source_platform}:{envelope.source_record_id}:{envelope.source_version}"
        )
        schema_bundle = self._schema.build(
            document_id,
            envelope.raw_text,
            envelope.job_title_raw or "未命名岗位",
        )
        rule_extraction = domain_to_extraction(schema_bundle.document).model_dump(mode="json")
        skill_item_types = {
            "programming_language": "language",
            "database": "technology",
            "framework": "framework",
            "platform": "platform",
            "tool": "tool",
            "method": "method",
        }
        requirements = []
        for item in rule_extraction["requirements"]:
            kind = item["kind"]
            common = {
                "requirement_id": item["requirement_id"],
                "kind": kind,
                "modality": item["modality"],
                "evidence": item["evidence"],
            }
            if kind == "skill":
                common["items"] = [
                    {
                        "name": skill["name"],
                        "item_type": skill_item_types.get(skill.get("item_type"), "other"),
                    }
                    for skill in item.get("items", [])
                ]
                common["proficiency"] = item.get("proficiency")
            elif kind == "tool":
                common["tools"] = item.get("tools", [])
            else:
                for field in (
                    "text",
                    "minimum_degree",
                    "majors",
                    "school_constraints",
                    "admission_type",
                    "graduation_year",
                    "student_cohort",
                    "minimum_years",
                    "maximum_years",
                    "domain",
                    "role",
                    "duration_text",
                    "experience_unlimited",
                    "certificates",
                    "skills",
                ):
                    if field in item:
                        common[field] = item[field]
            requirements.append(common)
        extraction = JDExtractionResult.model_validate(
            {
                "schema_version": "v2",
                "document_id": document_id,
                "job_title": (
                    {
                        "text": rule_extraction["job_title"]["value"],
                        "evidence": rule_extraction["job_title"]["evidence"],
                    }
                    if rule_extraction["job_title"] is not None
                    else None
                ),
                "responsibilities": [
                    {
                        "requirement_id": item["requirement_id"],
                        "text": item["action"],
                        "evidence": item["evidence"],
                    }
                    for item in rule_extraction["responsibilities"]
                ],
                "requirements": requirements,
                "company_facts": [
                    {
                        "fact_id": item["fact_id"],
                        "text": str(item["value"]),
                        "evidence": item["evidence"],
                    }
                    for item in rule_extraction["company_facts"]
                ],
                "employment_facts": [
                    {
                        "fact_id": item["fact_id"],
                        "fact_type": (
                            item["kind"]
                            if item["kind"]
                            in {
                                "location",
                                "employment_type",
                                "salary",
                                "headcount",
                                "schedule",
                            }
                            else "other"
                        ),
                        "text": str(item["value"]),
                        "evidence": item["evidence"],
                    }
                    for item in rule_extraction["employment_facts"]
                ],
            }
        )
        rule_normalized = domain_to_normalization(schema_bundle.normalization).model_dump(
            mode="json"
        )
        projection_items = []
        grouped_requirements: dict[str, dict[str, object]] = {}
        for item in rule_normalized["normalized_requirements"]:
            catalog_match = self._catalog_by_name.get(
                str(item.get("canonical_name") or item.get("source_name") or "").casefold()
            )
            if catalog_match is None:
                item["resolution_status"] = "unresolved"
                item["skill_id"] = None
                item["canonical_name"] = None
                continue
            skill_id, catalog_item = catalog_match
            item["skill_id"] = skill_id
            item["canonical_name"] = catalog_item["canonical_name"]
            item["resolution_status"] = "resolved"
            projection_items.append(
                SkillClassificationSetV1(
                    skill_id=skill_id,
                    canonical_name=catalog_item["canonical_name"],
                    classifications=catalog_item["classifications"],
                )
            )
            requirement = grouped_requirements.setdefault(
                item["requirement_id"],
                {
                    "requirement_id": item["requirement_id"],
                    "kind": item["requirement_kind"],
                    "normalized_skills": [],
                },
            )
            requirement["normalized_skills"].append(
                {
                    "source_name": item["source_name"],
                    "skill_id": item["skill_id"],
                    "canonical_name": item["canonical_name"],
                    "category_code": item["category_code"],
                    "subcategory_code": item["subcategory_code"],
                    "resolution_status": item["resolution_status"],
                    "resolution_source": item.get("resolution_source")
                    or (
                        "canonical_name"
                        if item["resolution_status"] == "resolved"
                        else "unresolved"
                    ),
                }
            )
        unresolved_items = [
            {
                "source_name": item["source_value"],
                "item_type": (
                    item["item_type"] if item["item_type"] in {"skill", "position"} else "position"
                ),
                "reason": item["reason"],
            }
            for item in rule_normalized["unresolved_items"]
        ]
        rule_salary = rule_normalized["salary"]
        normalized_salary = None
        if rule_salary is not None:
            normalized_salary = {
                "minimum": rule_salary.get("minimum"),
                "maximum": rule_salary.get("maximum"),
                "currency": rule_salary.get("currency") or "CNY",
                "period": rule_salary.get("period") or "unknown",
            }
        normalized = JDNormalizedResult.model_validate(
            {
                "schema_version": "v2",
                "document_id": document_id,
                "job_classification": rule_normalized["job_classification"]
                or {
                    "schema_version": "job-position-classification.v3",
                    "taxonomy_version": "position-taxonomy.v3.0.0",
                    "source_title": envelope.job_title_raw,
                    "classification_status": "catalog_gap",
                    "review_reason_codes": ["RULE_BASED_EXTRACTION_REQUIRES_REVIEW"],
                    "classification_policy_version": "position-classifier.v3.0",
                },
                "normalized_requirements": list(grouped_requirements.values()),
                "salary": normalized_salary,
                "unresolved_items": unresolved_items,
            }
        )
        projection = list({item.skill_id: item for item in projection_items}.values())
        finished_at = datetime.now(timezone.utc)
        return ExtractedJDBundleV2(
            source_platform=envelope.source_platform,
            source_record_id=envelope.source_record_id,
            source_version=envelope.source_version,
            cleaned_text=envelope.raw_text,
            extraction_result=extraction,
            normalized_result=normalized,
            review_flags=[
                {
                    "code": "RULE_BASED_EXTRACTION_REQUIRES_REVIEW",
                    "severity": "warning",
                    "message": "Rule-based extraction must be reviewed before publication.",
                }
            ],
            extraction_provider=self.name,
            model_version="not_applicable",
            extraction_run_id=f"rule:{document_id}:{started_at.isoformat()}",
            extraction_started_at=started_at,
            extraction_finished_at=finished_at,
            skill_taxonomy=SkillTaxonomyProjectionV1(
                taxonomy_version="skill-taxonomy-snapshot.v1",
                skills=projection,
            ),
            execution=JDExtractionExecutionV2(
                mode="rule",
                provider=self.name,
                model="not_applicable",
                prompt_version="not_applicable",
                algorithm_version="jd-rule-extraction.v1",
                schema_version="extracted-jd-bundle-v2",
                normalization_version="v2",
                started_at=started_at,
                finished_at=finished_at,
            ),
            need_review=True,
            confidence_level="limited",
        )
