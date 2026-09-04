from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.domain.json_types import (
    FrozenJsonArray,
    FrozenJsonObject,
    freeze_json,
    freeze_json_object,
)
from app.domain.permissions import permissions_for_role
from app.models.data_validation import DataValidationTask, ValidationReport
from app.models.extraction_task import ExtractionTask
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.jd_publication import JDPublication
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.models.matching_service_reference import MatchingServiceReference
from app.models.outbox_message import OutboxMessage
from app.models.position_cluster import PositionCluster
from app.models.resume import Resume
from app.models.resume_parse_result import ResumeParseResult
from app.models.resume_skill import ResumeSkill
from app.models.review_task import ReviewTask
from app.models.source_cv import CVExtractionTask, SourceCV, SourceCVVersion, ValidatedCVSnapshot
from app.models.source_jd import SourceJD, SourceJDVersion
from app.models.predicted_position import PredictedPosition
from app.models.task_record import TaskRecord
from app.models.trend_source import TrendSource


_DEMO_TASK_TYPES = frozenset(
    {"jd_extraction", "cv_extraction", "trend", "discovery", "matching"}
)
_TASK_TYPE_ALIASES = {
    "jd": "jd_extraction",
    "jd_extraction": "jd_extraction",
    "cv": "cv_extraction",
    "cv_extraction": "cv_extraction",
    "trend": "trend",
    "trend_analysis": "trend",
    "predicted_position_analysis": "trend",
    "discovery": "discovery",
    "position_cluster": "discovery",
    "matching": "matching",
    "match": "matching",
}
_STATUS_ALIASES = {
    "pending": "pending",
    "queued": "pending",
    "created": "pending",
    "submitted": "pending",
    "accepted": "pending",
    "waiting": "pending",
    "not_started": "pending",
    "reference_pending": "pending",
    "running": "running",
    "in_progress": "running",
    "processing": "running",
    "started": "running",
    "executing": "running",
    "succeeded": "succeeded",
    "success": "succeeded",
    "completed": "succeeded",
    "complete": "succeeded",
    "current": "succeeded",
    "available": "succeeded",
    "published": "succeeded",
    "done": "succeeded",
    "active": "succeeded",
    "failed": "failed",
    "error": "failed",
    "blocked": "failed",
    "rejected": "failed",
    "stale": "failed",
    "remote_unknown": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


def _canonical_task_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    result = _TASK_TYPE_ALIASES.get(normalized)
    if result is None:
        raise ValueError(f"Unsupported demo task type: {value}")
    return result


def _canonical_status(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    result = _STATUS_ALIASES.get(normalized)
    if result is None:
        raise ValueError(f"Unsupported demo task status: {value}")
    return result


def _progress(status: str, value: float | None = None) -> float:
    if value is not None:
        return max(0.0, min(1.0, float(value)))
    return {"pending": 0.0, "running": 0.5, "succeeded": 1.0}.get(status, 0.0)


def _error(code: object = None, message: object = None) -> dict | None:
    if code is None and message is None:
        return None
    return {
        "code": str(code) if code is not None else None,
        "message": str(message) if message is not None else None,
    }


def _timestamp(value) -> str | None:
    return value.isoformat() if value is not None else None


def _trusted_result_reference(
    value: object,
    *,
    fallback: str,
    resource_prefixes: tuple[str, ...],
) -> str:
    reference = str(value) if value is not None else ""
    return reference if reference.startswith(resource_prefixes) else fallback


def _demo_task(
    *,
    task_id: object,
    task_type: str,
    object_type: object,
    object_id: object,
    service: str,
    status: object,
    progress: float | None,
    error: dict | None,
    result_reference: object,
    created_at,
    updated_at,
) -> dict:
    canonical_status = _canonical_status(str(status))
    return {
        "task_id": str(task_id),
        "task_type": task_type,
        "object_type": str(object_type),
        "object_id": str(object_id),
        "service": service,
        "status": canonical_status,
        "progress": _progress(canonical_status, progress),
        "error": error,
        "result_reference": (
            str(result_reference) if result_reference is not None else None
        ),
        "created_at": _timestamp(created_at),
        "updated_at": _timestamp(updated_at),
    }


def _state(row, *, status: str | None = None, error_code=None, error_message=None):
    if row is None:
        return {"status": "not_started", "id": None, "error": None}
    code = error_code if error_code is not None else getattr(row, "last_error_code", None)
    message = (
        error_message
        if error_message is not None
        else getattr(row, "last_error_message", None)
    )
    return {
        "status": status or getattr(row, "status", "available"),
        "id": row.id,
        "error": {"code": code, "message": message} if code or message else None,
    }


def _action(
    code: str,
    method: str,
    endpoint: str,
    permission: str,
    enabled: bool,
    reason: str,
    *,
    input_required: tuple[str, ...] = (),
    allowed_permissions: frozenset[str] | None = None,
) -> dict:
    authorized = (
        allowed_permissions is None or permission in allowed_permissions
    )
    action = {
        "code": code,
        "method": method,
        "endpoint": endpoint,
        "permission": permission,
        "authorized": authorized,
        "enabled": enabled and authorized,
        "reason": (
            None
            if enabled and authorized
            else (
                reason
                if authorized
                else f"Missing permission: {permission}"
            )
        ),
    }
    if input_required:
        action["input"] = {"required": list(input_required)}
    return action


class SqlAlchemyIntegrationStatusReader:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_demo_tasks(
        self,
        *,
        task_type: str | None,
        status: str | None,
        object_id: str | None,
    ) -> FrozenJsonArray:
        selected_type = _canonical_task_type(task_type) if task_type else None
        selected_status = _canonical_status(status) if status else None
        if selected_type is not None and selected_type not in _DEMO_TASK_TYPES:
            raise ValueError(f"Unsupported demo task type: {selected_type}")

        with self._session_factory() as session:
            items: list[dict] = []
            matching_references = session.query(MatchingServiceReference).all()
            matching_task_ids = {row.task_id for row in matching_references}

            if selected_type in (None, "jd_extraction"):
                for row in session.query(ExtractionTask).all():
                    items.append(
                        _demo_task(
                            task_id=row.id,
                            task_type="jd_extraction",
                            object_type="source_jd_version",
                            object_id=row.source_jd_version_id,
                            service="jd-extraction",
                            status=row.status,
                            progress=None,
                            error=_error(
                                row.last_error_code, row.last_error_message
                            ),
                            result_reference=(
                                f"/api/v1/extraction-tasks/{row.id}"
                            ),
                            created_at=row.created_at,
                            updated_at=row.updated_at,
                        )
                    )

            if selected_type in (None, "cv_extraction"):
                for row in session.query(CVExtractionTask).all():
                    result_reference = (
                        f"/api/v1/validated-cv-snapshots/"
                        f"{row.latest_validated_cv_snapshot_id}"
                        if row.latest_validated_cv_snapshot_id
                        else f"/api/v1/cv-extraction-tasks/{row.id}"
                    )
                    items.append(
                        _demo_task(
                            task_id=row.id,
                            task_type="cv_extraction",
                            object_type="source_cv_version",
                            object_id=row.source_cv_version_id,
                            service="cv-extraction",
                            status=row.status,
                            progress=None,
                            error=_error(
                                row.last_error_code, row.last_error_message
                            ),
                            result_reference=result_reference,
                            created_at=row.created_at,
                            updated_at=row.updated_at,
                        )
                    )

            task_rows = (
                session.query(TaskRecord)
                .filter(
                    TaskRecord.task_type.in_(
                        (
                            "trend_analysis",
                            "predicted_position_analysis",
                            "position_cluster",
                            "match",
                        )
                    )
                )
                .all()
            )
            represented_discovery_runs: set[str] = set()
            for row in task_rows:
                result = row.result_payload or {}
                input_payload = row.input_payload or {}
                if row.task_type in {"trend_analysis", "predicted_position_analysis"}:
                    canonical_type = "trend"
                    if selected_type not in (None, canonical_type):
                        continue
                    if row.task_type == "trend_analysis":
                        item_object_type = "position"
                        item_object_id = input_payload.get("position_id") or row.id
                        fallback_reference = f"/api/v1/trend-analysis/tasks/{row.id}"
                    else:
                        item_object_type = "trend_run"
                        item_object_id = result.get("provider_run_id") or row.id
                        fallback_reference = (
                            f"/api/v1/predicted-positions/tasks/{row.id}"
                        )
                    items.append(
                        _demo_task(
                            task_id=row.id,
                            task_type=canonical_type,
                            object_type=item_object_type,
                            object_id=item_object_id,
                            service="trend-intelligence",
                            status=row.status,
                            progress=row.progress,
                            error=_error(row.error_code, row.error_message),
                            result_reference=_trusted_result_reference(
                                row.result_reference,
                                fallback=fallback_reference,
                                resource_prefixes=(
                                    "trend_report:",
                                    "trend-intelligence:",
                                ),
                            ),
                            created_at=row.created_at,
                            updated_at=row.updated_at,
                        )
                    )
                    continue

                if row.task_type == "position_cluster":
                    canonical_type = "discovery"
                    run_id = (
                        result.get("discovery_run_id")
                        or input_payload.get("discovery_run_id")
                    )
                    if run_id:
                        represented_discovery_runs.add(str(run_id))
                    if selected_type not in (None, canonical_type):
                        continue
                    items.append(
                        _demo_task(
                            task_id=row.id,
                            task_type=canonical_type,
                            object_type="discovery_run",
                            object_id=run_id or row.id,
                            service="emerging-discovery",
                            status=row.status,
                            progress=row.progress,
                            error=_error(row.error_code, row.error_message),
                            result_reference=_trusted_result_reference(
                                row.result_reference,
                                fallback=(
                                    f"discovery_run:{run_id}"
                                    if run_id
                                    else f"/api/v1/position-clusters/tasks/{row.id}"
                                ),
                                resource_prefixes=(
                                    "discovery_run:",
                                    "position_cluster:",
                                ),
                            ),
                            created_at=row.created_at,
                            updated_at=row.updated_at,
                        )
                    )
                    continue

                if row.task_type == "match" and row.id not in matching_task_ids:
                    canonical_type = "matching"
                    if selected_type not in (None, canonical_type):
                        continue
                    target_id = (
                        input_payload.get("target_id")
                        or input_payload.get("position_id")
                        or input_payload.get("resume_id")
                        or row.id
                    )
                    target_type = input_payload.get("target_type") or (
                        "resume" if input_payload.get("resume_id") else "position"
                    )
                    items.append(
                        _demo_task(
                            task_id=row.id,
                            task_type=canonical_type,
                            object_type=target_type,
                            object_id=target_id,
                            service="matching-service",
                            status=row.status,
                            progress=row.progress,
                            error=_error(row.error_code, row.error_message),
                            result_reference=_trusted_result_reference(
                                row.result_reference,
                                fallback=f"/api/v1/matches/tasks/{row.id}",
                                resource_prefixes=(
                                    "matching_evaluation:",
                                    "evaluation_report:",
                                ),
                            ),
                            created_at=row.created_at,
                            updated_at=row.updated_at,
                        )
                    )

            if selected_type in (None, "discovery"):
                grouped_runs: dict[str, list[PositionCluster]] = {}
                for row in session.query(PositionCluster).all():
                    if row.discovery_run_id:
                        grouped_runs.setdefault(row.discovery_run_id, []).append(row)
                for run_id, rows in grouped_runs.items():
                    if run_id in represented_discovery_runs:
                        continue
                    statuses = [_canonical_status(row.discovery_run_status or row.status) for row in rows]
                    run_status = (
                        "failed"
                        if "failed" in statuses
                        else "running"
                        if "running" in statuses
                        else "cancelled"
                        if "cancelled" in statuses
                        else "succeeded"
                        if statuses and all(value == "succeeded" for value in statuses)
                        else "pending"
                    )
                    assessment = rows[0].discovery_assessment or {}
                    raw_error = assessment.get("error")
                    if isinstance(raw_error, dict):
                        run_error = _error(
                            raw_error.get("code"), raw_error.get("message")
                        )
                    else:
                        run_error = _error(
                            assessment.get("error_code"),
                            raw_error or assessment.get("error_message"),
                        )
                    items.append(
                        _demo_task(
                            task_id=run_id,
                            task_type="discovery",
                            object_type="discovery_run",
                            object_id=run_id,
                            service="emerging-discovery",
                            status=run_status,
                            progress=None,
                            error=run_error,
                            result_reference=f"discovery_run:{run_id}",
                            created_at=min(row.created_at for row in rows),
                            updated_at=max(row.updated_at for row in rows),
                        )
                    )

            if selected_type in (None, "matching"):
                for row in matching_references:
                    canonical_status = _canonical_status(row.status)
                    items.append(
                        _demo_task(
                            task_id=row.task_id,
                            task_type="matching",
                            object_type=row.target_type,
                            object_id=row.position_id,
                            service=row.provider or "matching-service",
                            status=canonical_status,
                            progress=None,
                            error=(
                                _error(
                                    "MATCHING_TASK_FAILED",
                                    "matching task failed",
                                )
                                if canonical_status == "failed"
                                else None
                            ),
                            result_reference=(
                                f"/api/v1/matches/reports/{row.evaluation_id}"
                                if row.evaluation_id
                                else f"/api/v1/matches/tasks/{row.task_id}"
                            ),
                            created_at=row.created_at,
                            updated_at=row.updated_at,
                        )
                    )

        if selected_status is not None:
            items = [item for item in items if item["status"] == selected_status]
        if object_id is not None:
            items = [item for item in items if item["object_id"] == object_id]
        items.sort(
            key=lambda item: (item["created_at"] or "", item["task_id"]),
            reverse=True,
        )
        frozen = freeze_json(items, field="portal_demo_tasks")
        if not isinstance(frozen, FrozenJsonArray):
            raise TypeError("Portal demo task projection must be an array")
        return frozen

    def get(
        self,
        *,
        actor_role: str,
        jd_id: str | None,
        cv_task_id: str | None,
        trend_task_id: str | None = None,
    ) -> FrozenJsonObject:
        allowed_permissions = frozenset(permissions_for_role(actor_role))
        with self._session_factory() as session:
            result = {
                "jd": (
                    self._jd(session, jd_id, allowed_permissions)
                    if jd_id
                    else None
                ),
                "cv": (
                    self._cv(session, cv_task_id, allowed_permissions)
                    if cv_task_id
                    else None
                ),
            }
            if trend_task_id:
                result["trend"] = self._trend(
                    session, trend_task_id, allowed_permissions
                )
        return freeze_json_object(result, field="integration_status")

    @staticmethod
    def _trend(
        session: Session,
        task_id: str,
        allowed_permissions: frozenset[str],
    ) -> dict:
        task = session.get(TaskRecord, task_id)
        if task is None or task.task_type != "predicted_position_analysis":
            raise LookupError("Trend prediction task not found")
        result = task.result_payload or {}
        provider_run_id = result.get("provider_run_id")
        predictions = (
            session.query(PredictedPosition)
            .filter(PredictedPosition.provider_run_id == provider_run_id)
            .count()
            if provider_run_id
            else 0
        )
        sources = (
            session.query(TrendSource)
            .filter(TrendSource.provider_run_id == provider_run_id)
            .count()
            if provider_run_id
            else 0
        )
        return {
            "entity_id": task.id,
            "provider": {
                "name": result.get("provider"),
                "run_id": provider_run_id,
                "status": result.get("remote_status", task.status),
                "error": (
                    {"code": task.error_code, "message": task.error_message}
                    if task.error_code or task.error_message
                    else None
                ),
            },
            "projection": {
                "status": "available" if predictions else "not_started",
                "prediction_count": predictions,
                "source_count": sources,
                "source_coverage": result.get("source_coverage"),
                "missing_sources": result.get("missing_sources", []),
                "quality_flags": result.get("quality_flags", []),
            },
            "actions": [
                _action(
                    "sync_trend_run",
                    "GET",
                    f"/predicted-positions/tasks/{task.id}",
                    "trend.run.manage",
                    task.status in {"pending", "running"},
                    "Trend run is already terminal.",
                    allowed_permissions=allowed_permissions,
                )
            ],
        }

    @staticmethod
    def _jd(
        session: Session,
        jd_id: str,
        allowed_permissions: frozenset[str],
    ) -> dict:
        jd = session.get(JobDescription, jd_id)
        if jd is None:
            raise LookupError("JD not found")
        version = session.get(SourceJDVersion, jd.source_jd_version_id) if jd.source_jd_version_id else None
        source = session.get(SourceJD, version.source_jd_id) if version else None
        extraction = session.get(ExtractionTask, jd.extraction_task_id) if jd.extraction_task_id else None
        validation = (
            session.query(DataValidationTask)
            .filter(DataValidationTask.extraction_task_id == extraction.id)
            .order_by(DataValidationTask.created_at.desc())
            .first()
            if extraction else None
        )
        report = (
            session.query(ValidationReport)
            .filter(ValidationReport.data_validation_task_id == validation.id)
            .one_or_none()
            if validation else None
        )
        draft = session.query(JDParseResult).filter(JDParseResult.jd_id == jd.id).one_or_none()
        review = (
            session.query(ReviewTask)
            .filter(
                ReviewTask.object_id.in_([value for value in (jd.id, draft.id if draft else None) if value])
            )
            .order_by(ReviewTask.created_at.desc())
            .first()
        )
        publication = (
            session.query(JDPublication)
            .filter(JDPublication.parse_result_id == draft.id)
            .one_or_none()
            if draft else None
        )
        outbox = (
            session.query(OutboxMessage)
            .filter(OutboxMessage.aggregate_id == publication.id)
            .order_by(OutboxMessage.created_at.desc())
            .first()
            if publication else None
        )
        mapping = (
            session.query(KnowledgeGraphEntityMapping)
            .filter(
                KnowledgeGraphEntityMapping.entity_type == "document",
                KnowledgeGraphEntityMapping.main_system_id.in_(
                    [value for value in (publication.document_id if publication else None, jd.id) if value]
                ),
            )
            .order_by(KnowledgeGraphEntityMapping.updated_at.desc())
            .first()
        )
        discovered = False
        for row in session.query(PositionCluster.representative_jd_ids):
            if jd.id in (row.representative_jd_ids or []):
                discovered = True
                break
        matches = 0
        retry_enabled = bool(extraction and extraction.status == "failed" and extraction.attempt_count < extraction.max_attempts)
        review_enabled = bool(draft and draft.workflow_status == "draft" and review and review.status in {"pending", "claimed"})
        publish_enabled = bool(draft and draft.workflow_status == "reviewed")
        replay_enabled = bool(outbox and outbox.status == "failed")
        kg_enabled = bool(publication and mapping is None)
        discovery_enabled = bool(publication and not discovered)
        actions = [
            _action("retry_jd_extraction", "POST", f"/extraction-tasks/{extraction.id if extraction else jd.id}/retry", "integration.jd.retry", retry_enabled, "Extraction is not retryable.", allowed_permissions=allowed_permissions),
            _action("review_jd", "POST", f"/review-tasks/{review.id if review else jd.id}/{'approve' if review and review.status == 'claimed' else 'claim'}", "kg.review.manage", review_enabled, "No active draft review task is actionable.", allowed_permissions=allowed_permissions),
            _action("publish_jd", "POST", f"/jd-parse-results/{draft.id if draft else jd.id}/publish", "catalog.promote.manage", publish_enabled, "JD has not been approved.", allowed_permissions=allowed_permissions),
            _action("replay_outbox", "POST", f"/outbox-events/{outbox.id if outbox else jd.id}/requeue", "integration.outbox.requeue", replay_enabled, "Outbox message is not failed.", allowed_permissions=allowed_permissions),
            _action("sync_knowledge_graph", "POST", f"/integrations/knowledge-graph/jds/{publication.document_id if publication else jd.id}/sync", "kg.build.manage", kg_enabled, "Publication is absent or already mapped.", allowed_permissions=allowed_permissions),
            _action("run_discovery", "POST", "/position-clusters/tasks", "emerging.discovery.manage", discovery_enabled, "Published JD is already included or publication is absent.", allowed_permissions=allowed_permissions),
        ]
        return {
            "entity_id": jd.id,
            "source": _state(source, status="versioned" if version else "not_versioned"),
            "extraction": _state(extraction),
            "validation": _state(
                validation,
                status=(report.conclusion if report else (validation.status if validation else None)),
            ),
            "draft": _state(draft, status=draft.workflow_status if draft else None),
            "review": _state(review),
            "publication": _state(publication, status="published" if publication else None),
            "outbox": _state(
                outbox,
                error_message=outbox.last_error if outbox else None,
            ),
            "knowledge_graph": _state(
                mapping,
                status=mapping.sync_status if mapping else None,
                error_code=mapping.last_error_code if mapping else None,
                error_message=mapping.last_error_message if mapping else None,
            ),
            "discovery": {"status": "included" if discovered else "not_started", "id": None, "error": None},
            "matching": {"status": "available" if matches else "not_started", "id": None, "count": matches, "error": None},
            "actions": actions,
        }

    @staticmethod
    def _cv(
        session: Session,
        task_id: str,
        allowed_permissions: frozenset[str],
    ) -> dict:
        task = session.get(CVExtractionTask, task_id)
        if task is None:
            raise LookupError("CV extraction task not found")
        version = session.get(SourceCVVersion, task.source_cv_version_id)
        source = session.get(SourceCV, version.source_cv_id) if version else None
        snapshot = (
            session.query(ValidatedCVSnapshot)
            .filter(ValidatedCVSnapshot.cv_extraction_task_id == task.id)
            .one_or_none()
        )
        resume = session.get(Resume, task.resume_id) if task.resume_id else None
        parse_result = (
            session.query(ResumeParseResult)
            .filter(ResumeParseResult.resume_id == resume.id)
            .one_or_none()
            if resume
            else None
        )
        remote_rows = (
            session.query(MatchingServiceReference)
            .filter(MatchingServiceReference.resume_id == resume.id)
            .order_by(MatchingServiceReference.created_at.desc())
            .all()
            if resume
            else []
        )
        skill_rows = (
            session.query(ResumeSkill)
            .filter(ResumeSkill.resume_id == resume.id)
            .order_by(ResumeSkill.confidence.desc())
            .all()
            if resume
            else []
        )
        latest_remote = remote_rows[0] if remote_rows else None
        remote_available = bool(
            latest_remote
            and latest_remote.status in {"succeeded", "current"}
            and latest_remote.evaluation_id
        )
        match_count = int(remote_available)
        retry_enabled = (
            task.status == "failed"
            and task.retryable
            and task.attempt_count < task.max_attempts
        )
        review_flags = (
            task.validation_report_payload.get("review_flags", [])
            if isinstance(task.validation_report_payload, dict)
            else []
        )
        actions = [
            _action(
                "retry_cv_extraction",
                "POST",
                f"/portal/admin/integration-status/cv-extraction-tasks/{task.id}/retry",
                "integration.cv.retry",
                retry_enabled,
                "Only failed, retryable CV extraction tasks can be queued.",
                allowed_permissions=allowed_permissions,
            ),
            _action(
                "confirm_cv_parse_result",
                "POST",
                f"/resumes/{resume.id if resume else task.id}/parse-result/confirm",
                "resume.parse.manage",
                bool(parse_result and parse_result.need_review),
                "No parse result is waiting for confirmation.",
                allowed_permissions=allowed_permissions,
            ),
            _action(
                "generate_resume_skill_profile",
                "POST",
                f"/resumes/{resume.id if resume else task.id}/skill-profile",
                "resume.profile.generate",
                bool(
                    resume
                    and parse_result
                    and not parse_result.need_review
                    and not skill_rows
                ),
                "Confirm the parse result before generating a skill profile.",
                allowed_permissions=allowed_permissions,
            ),
            _action(
                "create_match",
                "POST",
                "/matches/tasks",
                "matching.run",
                bool(
                    resume
                    and parse_result
                    and not parse_result.need_review
                    and skill_rows
                ),
                "Confirm the parse result and select a target position.",
                input_required=("target_id",),
                allowed_permissions=allowed_permissions,
            ),
            _action(
                "create_learning_path",
                "POST",
                "/learning-paths",
                "learning_path.create",
                bool(remote_available),
                "A current matching evaluation is required.",
                input_required=("evaluation_id",),
                allowed_permissions=allowed_permissions,
            ),
        ]
        return {
            "entity_id": task.id,
            "source": _state(source, status="versioned" if version else "not_versioned"),
            "extraction": _state(task),
            "validation": {
                **_state(snapshot, status=task.validation_conclusion),
                "details": {"review_flags": review_flags},
            },
            "draft": _state(
                resume,
                status=(
                    "needs_review"
                    if parse_result is not None and parse_result.need_review
                    else ("ready" if resume else None)
                ),
            ) | {
                "details": {
                    "resume_id": resume.id if resume else None,
                    "parse_result_id": parse_result.id if parse_result else None,
                    "skill_count": len(skill_rows),
                    "skills": [
                        {
                            "skill_id": row.skill_id,
                            "name": row.raw_skill,
                            "confidence": row.confidence,
                        }
                        for row in skill_rows[:10]
                    ],
                }
            },
            "review": {
                "status": (
                    "pending_confirmation"
                    if parse_result is not None and parse_result.need_review
                    else ("confirmed" if parse_result is not None else "not_applicable")
                ),
                "id": parse_result.id if parse_result else None,
                "error": None,
            },
            "publication": {"status": "not_applicable", "id": None, "error": None},
            "outbox": {"status": "not_applicable", "id": None, "error": None},
            "knowledge_graph": {"status": "not_applicable", "id": None, "error": None},
            "discovery": {"status": "not_applicable", "id": None, "error": None},
            "matching": {
                "status": (
                    "available"
                    if match_count
                    else latest_remote.status
                    if latest_remote and latest_remote.status in {"pending", "running", "failed", "stale"}
                    else "not_started"
                ),
                "id": (
                    latest_remote.evaluation_id or latest_remote.task_id
                    if latest_remote
                    else None
                ),
                "count": match_count,
                "error": (
                    {"code": "MATCHING_TASK_FAILED", "message": "matching task failed"}
                    if latest_remote and latest_remote.status == "failed"
                    else None
                ),
                "details": {
                    "resume_id": resume.id if resume else None,
                    "stale_count": 0,
                    "report": (
                        {
                            "evaluation_id": latest_remote.evaluation_id,
                            "task_id": latest_remote.task_id,
                            "status": latest_remote.status,
                            "target_type": latest_remote.target_type,
                            "target_id": latest_remote.position_id,
                            "lineage": {
                                "provider": latest_remote.provider,
                                "algorithm_version": latest_remote.algorithm_version,
                                "source_version": latest_remote.source_version,
                            },
                        }
                        if latest_remote and latest_remote.evaluation_id
                        else None
                    ),
                },
            },
            "actions": actions,
        }
