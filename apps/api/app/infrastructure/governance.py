from __future__ import annotations

from sqlalchemy import case, func, update
from sqlalchemy.orm import Session, sessionmaker

from app.models.evidence_source import EvidenceSource
from app.models.review_task import ReviewTask
from app.models.review_task_event import ReviewTaskEvent
from app.models.rag_generation import RagGeneration
from app.models.jd_parse_result import JDParseResult
from app.models.jd import JobDescription
from app.models.data_validation import DataValidationTask, ValidationReport
from app.models.user import User
from app.domain.jd_skill_catalog import (
    SkillCatalogGateError,
    require_catalog_binding,
)
from app.infrastructure.data_validation import load_catalog_entries
from app.infrastructure.jd_schema import VersionedJDSchemaAdapter
from app.contexts.governance_feedback import (
    EvidenceDraft,
    EvidenceRecord,
    ReviewEventRecord,
    ReviewRecord,
    RagGenerationRecord,
)
from app.domain.json_types import FrozenJsonObject, freeze_json_object, thaw_json_object
from app.domain.text_cleaning import clean_jd_text_for_display


class SqlAlchemyEvidenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: EvidenceDraft) -> EvidenceRecord:
        row = EvidenceSource(**draft.__dict__)
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def list(self) -> list[EvidenceRecord]:
        rows = self._session.query(EvidenceSource).order_by(EvidenceSource.created_at.desc()).all()
        return [self._record(row) for row in rows]

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        row = self._session.get(EvidenceSource, evidence_id)
        return self._record(row) if row is not None else None

    def update(self, evidence_id: str, changes: dict[str, object]) -> EvidenceRecord:
        row = self._required(evidence_id)
        for key, value in changes.items():
            setattr(row, key, value)
        self._session.flush()
        return self._record(row)

    def delete(self, evidence_id: str) -> None:
        self._session.delete(self._required(evidence_id))

    def related(self, object_type: str, object_id: str) -> list[EvidenceRecord]:
        rows = (
            self._session.query(EvidenceSource)
            .filter(
                EvidenceSource.related_object_type == object_type,
                EvidenceSource.related_object_id == object_id,
            )
            .order_by(EvidenceSource.created_at.desc())
            .all()
        )
        return [self._record(row) for row in rows]

    def _required(self, evidence_id: str) -> EvidenceSource:
        row = self._session.get(EvidenceSource, evidence_id)
        if row is None:
            raise LookupError(evidence_id)
        return row

    @staticmethod
    def _record(row: EvidenceSource) -> EvidenceRecord:
        return EvidenceRecord(
            row.id,
            row.source_type,
            row.source_name,
            row.title,
            row.url,
            row.raw_text,
            row.publish_date,
            row.credibility_score,
            row.related_object_type,
            row.related_object_id,
            row.created_at,
            row.updated_at,
            row.source_platform,
            row.enterprise_id,
            row.template_cluster_id,
            row.source_version,
            row.source_fact_id,
            row.source_jd_id,
            row.source_jd_version_id,
        )


class SqlAlchemyReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, object_type: str, object_id: str, priority: str, reason: str | None
    ) -> ReviewRecord:
        active = self._active_for_object(object_type, object_id)
        if active is not None:
            raise RuntimeError("An active review task already exists for this object")
        row = ReviewTask(
            object_type=object_type, object_id=object_id, priority=priority, reason=reason
        )
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def ensure_active(
        self, parse_result_id: str, *, reason: str, priority: str = "normal"
    ) -> str:
        active = self._active_for_object("jd_parse_result", parse_result_id)
        if active is not None:
            if priority == "high" and active.priority in {"low", "normal"}:
                active.priority = "high"
                active.reason = reason
                self._session.flush()
            return active.id
        row = ReviewTask(
            object_type="jd_parse_result",
            object_id=parse_result_id,
            priority=priority,
            reason=reason,
            status="pending",
        )
        self._session.add(row)
        self._session.flush()
        self._session.add(
            ReviewTaskEvent(
                task_id=row.id,
                actor_user_id="system:jd-lifecycle",
                action="create",
                before_status=None,
                after_status="pending",
                comment=reason,
                payload_snapshot={
                    "object_type": row.object_type,
                    "object_id": row.object_id,
                },
            )
        )
        self._session.flush()
        return row.id

    def approve_active(
        self,
        parse_result_id: str,
        *,
        task_id: str | None = None,
        actor_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> None:
        parsed = self._session.get(JDParseResult, parse_result_id)
        if parsed is None:
            raise LookupError(parse_result_id)
        active = self._active_for_object("jd_parse_result", parse_result_id)
        if active is None:
            approved = (
                self._session.query(ReviewTask)
                .filter(
                    ReviewTask.object_type == "jd_parse_result",
                    ReviewTask.object_id == parse_result_id,
                    ReviewTask.status == "approved",
                )
                .order_by(ReviewTask.created_at.desc(), ReviewTask.id.desc())
                .first()
            )
            if approved is not None and parsed.workflow_status in {"reviewed", "published"}:
                return
            raise RuntimeError("JD parse result has no active review task")
        if task_id is not None and active.id != task_id:
            raise RuntimeError("Requested review task is not the active task")
        if (
            active.status == "claimed"
            and active.reviewer_id != actor_id
            and actor_role not in {"admin", "developer"}
        ):
            raise RuntimeError("Claimed review task belongs to another reviewer")
        if parsed.workflow_status == "published":
            raise RuntimeError("Published JD results are immutable")
        self._assert_jd_parse_approvable(parsed)
        if active.status == "pending":
            self.transition(
                active.id,
                actor_id=actor_id,
                action="claim",
                status="claimed",
            )
            active = self._active_for_object("jd_parse_result", parse_result_id)
            if active is None or (task_id is not None and active.id != task_id):
                raise RuntimeError("JD parse review task claim failed")
        claim_task_id = active.id
        parsed.workflow_status = "reviewed"
        parsed.need_review = False
        self.transition(
            claim_task_id,
            actor_id=actor_id,
            action="approve",
            status="approved",
            comment=comment,
        )
        from app.core.config import settings

        if settings.DATA_VALIDATION_MODE == "enforce":
            from app.infrastructure.jd_repository import SqlAlchemyJDUoW
            validation_uow = SqlAlchemyJDUoW(
                lambda: self._session,
                close_session=False,
                data_validation_mode="enforce",
            )
            with validation_uow:
                validation_uow.stage_validation_for_parse_result(parsed.id)

    def validate_approve_active(
        self,
        parse_result_id: str,
        *,
        task_id: str,
        actor_id: str,
        actor_role: str,
    ) -> None:
        parsed = self._session.get(JDParseResult, parse_result_id)
        if parsed is None:
            raise LookupError(parse_result_id)
        active = self._active_for_object("jd_parse_result", parse_result_id)
        if active is None or active.id != task_id:
            raise RuntimeError("Requested review task is not the active task")
        if (
            active.status == "claimed"
            and active.reviewer_id != actor_id
            and actor_role not in {"admin", "developer"}
        ):
            raise RuntimeError("Claimed review task belongs to another reviewer")
        if parsed.workflow_status == "published":
            raise RuntimeError("Published JD results are immutable")
        self._assert_jd_parse_approvable(parsed)

    def _assert_jd_parse_approvable(self, parsed: JDParseResult) -> None:
        if parsed.extraction_result is None or parsed.normalized_result is None:
            raise RuntimeError("Versioned extraction and normalization must exist before review")
        try:
            bundle = VersionedJDSchemaAdapter().load(
                parsed.extraction_result,
                parsed.normalized_result,
                schema_version=parsed.schema_version,
                normalization_schema_version=parsed.normalization_schema_version,
            )
            VersionedJDSchemaAdapter().validate_publishable(bundle)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(str(exc)) from exc
        catalog, _ = load_catalog_entries(self._session)
        try:
            for item in bundle.normalization.items:
                if item.item_type != "skill":
                    continue
                if item.resolution_status not in {"resolved", "manually_confirmed"}:
                    continue
                require_catalog_binding(
                    resolution_status=item.resolution_status,
                    skill_id=item.skill_id,
                    canonical_name=item.canonical_name,
                    skills=catalog,
                )
        except SkillCatalogGateError as exc:
            raise RuntimeError(exc.code) from exc
        if any(
            flag.severity == "blocking" and flag.flag_type != "skill"
            for flag in bundle.normalization.review_flags
        ):
            raise RuntimeError("Blocking review flags must be resolved before confirmation")

    def _active_for_object(self, object_type: str, object_id: str) -> ReviewTask | None:
        rows = (
            self._session.query(ReviewTask)
            .filter(
                ReviewTask.object_type == object_type,
                ReviewTask.object_id == object_id,
                ReviewTask.status.in_(("pending", "claimed")),
            )
            .order_by(ReviewTask.created_at.asc(), ReviewTask.id.asc())
            .all()
        )
        if len(rows) > 1:
            raise RuntimeError("Multiple active review tasks exist for this object")
        return rows[0] if rows else None

    def list(self) -> list[ReviewRecord]:
        rows = self._session.query(ReviewTask).order_by(ReviewTask.created_at.desc()).all()
        return self._records(rows)

    def list_page(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        task_kind: str | None = None,
    ) -> tuple[list[ReviewRecord], int]:
        query = self._session.query(ReviewTask)
        if status is not None:
            query = query.filter(ReviewTask.status == status)
        if task_kind is not None:
            query = query.filter(ReviewTask.object_type == task_kind)
        total = query.count()
        rows = (
            query.order_by(
                case(
                    (ReviewTask.status.in_(("pending", "claimed")), 0),
                    else_=1,
                ),
                ReviewTask.created_at.desc(),
                ReviewTask.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return self._records(rows), total

    def counts_by_status(self) -> dict[str, int]:
        rows = (
            self._session.query(ReviewTask.status, func.count(ReviewTask.id))
            .group_by(ReviewTask.status)
            .all()
        )
        counts = {
            status: 0
            for status in ("pending", "claimed", "approved", "rejected", "modified")
        }
        counts.update({status: int(count) for status, count in rows})
        return counts

    def _records(self, rows: list[ReviewTask]) -> list[ReviewRecord]:
        reviewer_ids = [row.reviewer_id for row in rows if row.reviewer_id]
        reviewer_names: dict[str, str] = {}
        if reviewer_ids:
            reviewer_names = dict(
                self._session.query(User.id, User.username)
                .filter(User.id.in_(reviewer_ids))
                .all()
            )
        object_info: dict[str, tuple[str | None, str]] = {}
        parse_ids = [
            row.object_id for row in rows if row.object_type == "jd_parse_result"
        ]
        if parse_ids:
            parsed_rows = (
                self._session.query(
                    JDParseResult.id,
                    JDParseResult.position_title,
                    JDParseResult.normalized_result,
                    JobDescription.title,
                )
                .join(JobDescription, JobDescription.id == JDParseResult.jd_id)
                .filter(JDParseResult.id.in_(parse_ids))
                .all()
            )
            for parse_id, position_title, normalized, jd_title in parsed_rows:
                object_info[parse_id] = (
                    position_title or jd_title,
                    self._review_stage(normalized),
                )
        report_ids = [
            row.object_id
            for row in rows
            if row.object_type == "data_validation_report"
        ]
        if report_ids:
            report_rows = (
                self._session.query(ValidationReport.id, JobDescription.title)
                .join(
                    DataValidationTask,
                    DataValidationTask.id
                    == ValidationReport.data_validation_task_id,
                )
                .join(
                    JobDescription,
                    JobDescription.extraction_task_id
                    == DataValidationTask.extraction_task_id,
                )
                .filter(ValidationReport.id.in_(report_ids))
                .all()
            )
            for report_id, jd_title in report_rows:
                object_info[report_id] = (jd_title, "数据质量")
        return [
            self._record(
                row,
                reviewer_name=reviewer_names.get(row.reviewer_id or ""),
                object_name=object_info.get(row.object_id, (None, "其他"))[0],
                review_stage=object_info.get(row.object_id, (None, "其他"))[1],
            )
            for row in rows
        ]

    def get(self, task_id: str) -> ReviewRecord | None:
        row = self._session.get(ReviewTask, task_id)
        return self._record(row) if row is not None else None

    def context(self, task_id: str) -> FrozenJsonObject:
        task = self._required(task_id)
        if task.object_type == "jd_parse_result":
            parsed = self._session.get(JDParseResult, task.object_id)
            if parsed is None:
                raise LookupError(task.object_id)
            jd = self._session.get(JobDescription, parsed.jd_id)
            normalized = parsed.normalized_result or {}
            extraction = parsed.extraction_result or {}
            skills = [dict(item) for item in normalized.get("normalized_requirements", [])]
            normalized_skill_keys = {
                (item.get("requirement_id"), item.get("source_name"))
                for item in skills
            }
            for requirement in extraction.get("requirements", []):
                if requirement.get("kind") != "skill":
                    continue
                requirement_id = requirement.get("requirement_id")
                for source_item in requirement.get("items", []):
                    source_name = source_item.get("name")
                    if (requirement_id, source_name) in normalized_skill_keys:
                        continue
                    skills.append(
                        {
                            "requirement_id": requirement_id,
                            "requirement_kind": "skill",
                            "source_name": source_name,
                            "skill_id": None,
                            "canonical_name": None,
                            "resolution_status": "unresolved",
                            "resolution_source": "not_normalized",
                        }
                    )
            resolved = [
                item for item in skills
                if item.get("resolution_status") in {"resolved", "manually_confirmed"}
            ]
            unresolved = [
                item for item in skills
                if item.get("resolution_status") in {"unresolved", "ambiguous", "pending", "conflict"}
            ]
            rejected = [item for item in skills if item.get("resolution_status") == "rejected"]
            blocking = [
                dict(item) for item in normalized.get("unresolved_items", [])
                if item.get("severity") == "blocking" and item.get("item_type") != "skill"
            ]
            review_flags = [
                dict(item) for item in normalized.get("unresolved_items", [])
            ]
            review_flags.extend(
                dict(item) for item in normalized.get("review_flags", [])
            )
            evidence = self._collect_evidence(extraction)
            position_classification = dict(
                normalized.get("job_classification") or {}
            )
            position_publishable = position_classification.get(
                "classification_status"
            ) in {"resolved", "manually_confirmed"} and bool(
                position_classification.get("position_id")
                and position_classification.get("position_code")
            )
            if not position_publishable and not any(
                item.get("code") == "job_classification_unresolved"
                for item in blocking
            ):
                blocking.append(
                    {
                        "item_type": "job_title",
                        "code": "job_classification_unresolved",
                        "severity": "blocking",
                        "reason": "岗位分类未人工确认或缺少有效目录绑定",
                    }
                )
            validation_pending: list[dict] = []
            if jd is not None and jd.extraction_task_id:
                validation_task_ids = [
                    row[0]
                    for row in self._session.query(DataValidationTask.id)
                    .filter(DataValidationTask.extraction_task_id == jd.extraction_task_id)
                    .all()
                ]
                if validation_task_ids:
                    report_rows = (
                        self._session.query(ValidationReport.id, ValidationReport.conclusion)
                        .filter(
                            ValidationReport.data_validation_task_id.in_(validation_task_ids)
                        )
                        .all()
                    )
                    conclusion_by_report = {
                        report_id: conclusion for report_id, conclusion in report_rows
                    }
                    report_ids = list(conclusion_by_report)
                    if report_ids:
                        review_rows = (
                            self._session.query(ReviewTask)
                            .filter(
                                ReviewTask.object_type == "data_validation_report",
                                ReviewTask.object_id.in_(report_ids),
                                ReviewTask.status.in_(("pending", "claimed")),
                            )
                            .all()
                        )
                        validation_pending = [
                            {
                                "task_id": row.id,
                                "report_id": row.object_id,
                                "conclusion": conclusion_by_report.get(row.object_id),
                                "reason": row.reason,
                                "status": row.status,
                            }
                            for row in review_rows
                        ]
            return freeze_json_object({
                "kind": "jd_parse_result",
                "jd_id": parsed.jd_id,
                "parse_result_id": parsed.id,
                "title": jd.title if jd is not None else parsed.position_title,
                "source_name": jd.source_name if jd is not None else None,
                "raw_text": (
                    jd.cleaned_text or clean_jd_text_for_display(jd.raw_text)
                    if jd is not None
                    else None
                ),
                "position": position_classification,
                "responsibilities": list(extraction.get("responsibilities") or []),
                "requirements": [
                    dict(item)
                    for item in extraction.get("requirements", [])
                    if item.get("kind") != "skill"
                ],
                "company_facts": list(extraction.get("company_facts") or []),
                "employment_facts": list(extraction.get("employment_facts") or []),
                "skills": skills,
                "resolved_skill_count": len(resolved),
                "unresolved_skill_count": len(unresolved),
                "rejected_skill_count": len(rejected),
                "blocking_issues": blocking,
                "risk_level": self._risk_level(
                    task.priority,
                    any(item.get("severity") == "blocking" for item in review_flags),
                ),
                "evidence": evidence,
                "review_flags": review_flags,
                "pending_validation_reviews": validation_pending,
                "can_approve": not blocking and position_publishable,
                "workflow_status": parsed.workflow_status,
            }, field="review_context")
        if task.object_type == "data_validation_report":
            report = self._session.get(ValidationReport, task.object_id)
            if report is None:
                raise LookupError(task.object_id)
            payload = dict(report.report_payload or {})
            review_flags = list(payload.get("review_flags") or payload.get("issues") or [])
            return freeze_json_object({
                "kind": "data_validation_report",
                "conclusion": report.conclusion,
                "policy_version": report.policy_version,
                "report": payload,
                "risk_level": {
                    "block": "high", "warn": "medium", "pass": "low"
                }.get(report.conclusion, self._risk_level(task.priority)),
                "evidence": self._collect_evidence(payload),
                "review_flags": review_flags,
            }, field="review_context")
        evidence_rows = (
            self._session.query(EvidenceSource)
            .filter(
                EvidenceSource.related_object_type == task.object_type,
                EvidenceSource.related_object_id == task.object_id,
            )
            .order_by(EvidenceSource.created_at.desc(), EvidenceSource.id.asc())
            .all()
        )
        return freeze_json_object({
            "kind": task.object_type,
            "risk_level": self._risk_level(task.priority),
            "evidence": [
                {
                    "evidence_id": row.id,
                    "source_type": row.source_type,
                    "title": row.title,
                    "url": row.url,
                    "credibility_score": row.credibility_score,
                }
                for row in evidence_rows
            ],
            "review_flags": [],
        }, field="review_context")

    def unresolved_skills(self) -> list[FrozenJsonObject]:
        rows = (
            self._session.query(JDParseResult, JobDescription)
            .join(JobDescription, JobDescription.id == JDParseResult.jd_id)
            .filter(
                JDParseResult.workflow_status != "published",
                JDParseResult.normalized_result.is_not(None),
            )
            .order_by(JDParseResult.created_at.desc())
            .all()
        )
        result: list[FrozenJsonObject] = []
        for parsed, jd in rows:
            normalized = parsed.normalized_result or {}
            reasons = {
                (item.get("source_name"), item.get("requirement_id")): item.get("reason")
                for item in normalized.get("unresolved_items", [])
                if isinstance(item, dict)
            }
            for item in normalized.get("normalized_requirements", []):
                if item.get("resolution_status") not in {"unresolved", "ambiguous", "pending", "conflict"}:
                    continue
                requirement_id = item.get("requirement_id")
                source_name = str(item.get("source_name") or "").strip()
                if not source_name:
                    continue
                result.append(freeze_json_object({
                    "id": f"{parsed.id}:{requirement_id or '-'}:{source_name}",
                    "parse_result_id": parsed.id,
                    "jd_id": parsed.jd_id,
                    "jd_title": jd.title,
                    "source_name": source_name,
                    "requirement_id": requirement_id,
                    "reason": reasons.get((source_name, requirement_id)) or "未匹配到唯一的标准技能",
                    "source_type": jd.source_type,
                    "source_name_label": jd.source_name,
                    "raw_text": jd.cleaned_text or clean_jd_text_for_display(jd.raw_text),
                }, field="unresolved_skill"))
        return result

    def transition(
        self,
        task_id: str,
        *,
        actor_id: str,
        action: str,
        status: str,
        comment: str | None = None,
        modified_payload: dict | None = None,
    ) -> ReviewRecord:
        row = self._required(task_id)
        before = row.status if action != "create" else None
        if action == "claim":
            changed = self._session.execute(
                update(ReviewTask)
                .where(ReviewTask.id == task_id, ReviewTask.status == "pending")
                .values(status="claimed", reviewer_id=actor_id)
            ).rowcount
            if changed != 1:
                raise RuntimeError("Review task was claimed concurrently")
            self._session.expire(row)
        elif action in {"approve", "reject", "modify"}:
            changed = self._session.execute(
                update(ReviewTask)
                .where(
                    ReviewTask.id == task_id,
                    ReviewTask.status == "claimed",
                    ReviewTask.reviewer_id == actor_id,
                )
                .values(
                    status=status,
                    reviewer_id=actor_id,
                    review_comment=comment,
                    modified_payload=modified_payload,
                )
            ).rowcount
            if changed != 1:
                raise RuntimeError("Review task terminal transition lost a concurrent race")
            row.status = status
            row.reviewer_id = actor_id
            row.review_comment = comment
            row.modified_payload = modified_payload
        else:
            row.status = status
        if action == "release":
            row.reviewer_id = None
        elif action not in {"create", "claim"}:
            row.reviewer_id = actor_id
        if comment is not None and action != "create":
            row.review_comment = comment
        if action == "modify":
            row.modified_payload = modified_payload
        self._session.add(
            ReviewTaskEvent(
                task_id=task_id,
                actor_user_id=actor_id,
                action=action,
                before_status=before,
                after_status=status,
                comment=comment,
                payload_snapshot=modified_payload,
            )
        )
        self._session.flush()
        return self._record(row)

    def history(self, task_id: str) -> list[ReviewEventRecord]:
        rows = (
            self._session.query(ReviewTaskEvent)
            .filter(ReviewTaskEvent.task_id == task_id)
            .order_by(ReviewTaskEvent.created_at.asc())
            .all()
        )
        return [
            ReviewEventRecord(
                row.id,
                row.task_id,
                row.actor_user_id,
                row.action,
                row.before_status,
                row.after_status,
                row.comment,
                row.payload_snapshot,
                row.created_at,
            )
            for row in rows
        ]

    def set_jd_parse_review_status(
        self,
        parse_result_id: str,
        *,
        workflow_status: str,
        need_review: bool,
    ) -> None:
        if workflow_status == "reviewed":
            raise RuntimeError("Reviewed status may only be written by ReviewTask approval")
        row = self._session.get(JDParseResult, parse_result_id)
        if row is None:
            raise LookupError(parse_result_id)
        if row.workflow_status == "published":
            raise RuntimeError("Published JD results are immutable")
        row.workflow_status = workflow_status
        row.need_review = need_review
        self._session.flush()

    def _required(self, task_id: str) -> ReviewTask:
        row = self._session.get(ReviewTask, task_id)
        if row is None:
            raise LookupError(task_id)
        return row

    @staticmethod
    def _review_stage(normalized: object | None) -> str:
        data = normalized if isinstance(normalized, dict) else {}
        classification = data.get("job_classification") or {}
        if classification.get("classification_status") not in {
            "resolved",
            "manually_confirmed",
        }:
            return "标准岗位"
        for item in data.get("unresolved_items", []):
            if (
                item.get("severity") == "blocking"
                and item.get("item_type") != "skill"
            ):
                return "结构化内容"
        return "内容核对"

    @staticmethod
    def _risk_level(priority: str, blocking: bool = False) -> str:
        if blocking or priority in {"urgent", "high"}:
            return "high"
        if priority == "low":
            return "low"
        return "medium"

    @staticmethod
    def _collect_evidence(value: object) -> list[dict]:
        result: list[dict] = []

        def visit(current: object) -> None:
            if isinstance(current, dict):
                for key, child in current.items():
                    if key == "evidence":
                        if isinstance(child, dict):
                            result.append(dict(child))
                        elif isinstance(child, list):
                            result.extend(dict(item) for item in child if isinstance(item, dict))
                    else:
                        visit(child)
            elif isinstance(current, list):
                for child in current:
                    visit(child)

        visit(value)
        return result

    @staticmethod
    def _record(
        row: ReviewTask,
        *,
        reviewer_name: str | None = None,
        object_name: str | None = None,
        review_stage: str | None = None,
    ) -> ReviewRecord:
        return ReviewRecord(
            row.id,
            row.object_type,
            row.object_id,
            row.priority,
            row.reason,
            row.status,
            row.reviewer_id,
            row.review_comment,
            row.modified_payload,
            row.created_at,
            row.updated_at,
            reviewer_name,
            object_name,
            review_stage,
        )


class SqlAlchemyRagGenerationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, **values) -> RagGenerationRecord:
        row = RagGeneration(status="draft", **values)
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def get(self, generation_id: str) -> RagGenerationRecord | None:
        row = self._session.get(RagGeneration, generation_id)
        return self._record(row) if row is not None else None

    def update_text(self, generation_id: str, text: str) -> RagGenerationRecord:
        row = self._required(generation_id)
        row.text = text
        row.need_review = True
        self._session.flush()
        return self._record(row)

    def confirm(self, generation_id: str, actor_id: str) -> RagGenerationRecord:
        row = self._required(generation_id)
        row.status = "confirmed"
        row.need_review = False
        row.confirmed_by = actor_id
        self._session.flush()
        return self._record(row)

    def list_need_review(self) -> list[RagGenerationRecord]:
        rows = (
            self._session.query(RagGeneration)
            .filter(RagGeneration.need_review.is_(True))
            .order_by(RagGeneration.created_at.desc())
            .all()
        )
        return [self._record(row) for row in rows]

    def _required(self, generation_id: str) -> RagGeneration:
        row = self._session.get(RagGeneration, generation_id)
        if row is None:
            raise LookupError(generation_id)
        return row

    @staticmethod
    def _record(row: RagGeneration) -> RagGenerationRecord:
        return RagGenerationRecord(
            row.id,
            row.prompt,
            row.text,
            tuple(row.evidence_ids or ()),
            tuple(row.citations or ()),
            row.need_review,
            row.status,
            row.created_by,
            row.confirmed_by,
            row.created_at,
            row.updated_at,
        )


class SqlAlchemyGovernanceUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyGovernanceUnitOfWork":
        self._session = self._session_factory()
        self.evidence = SqlAlchemyEvidenceRepository(self._session)
        self.reviews = SqlAlchemyReviewRepository(self._session)
        self.rag = SqlAlchemyRagGenerationRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()


class EvidenceRetrieverAdapter:
    def __init__(self, retriever) -> None:
        self._retriever = retriever

    def retrieve(
        self, query: str, documents: tuple[FrozenJsonObject, ...], top_k: int
    ) -> tuple[FrozenJsonObject, ...]:
        result = self._retriever.retrieve(
            query, [thaw_json_object(item) for item in documents], top_k
        )
        return tuple(freeze_json_object(item) for item in result)

    def metadata(self) -> tuple[str, str]:
        status = self._retriever.status()
        return status.implementation_status, status.provider
