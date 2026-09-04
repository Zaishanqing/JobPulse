from app.domain.json_types import FrozenJsonObject, freeze_json_object
from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import Callable

from app.domain.accounts import AccountActor
from app.domain.resumes import (
    RESUME_READ_ROLES,
    RESUME_WRITE_ROLES,
    ResumeRuleViolation,
    extract_skill_candidates,
    require_personal_role,
)
from app.contexts.talent_acquisition._ports.resumes import (
    JsonSections,  # noqa: F401 - public compatibility export
    ParseResultChanges,
    ParseResultDraft,
    ParseResultRecord,
    ResumeDraft,
    ResumeInputExtractionPort,
    ResumeRecord,
    ResumeSkillDraft,
    ResumeSkillRecord,
    ResumeUnitOfWork,
)
from app.contexts.platform import FileUploadWorkflowPort
from app.contexts.tasks import TaskPayload, TaskRecord, TaskWorkflowPort
from app.domain.errors import PermissionDenied
from app.profile_index_events import (
    enterprise_projection_entity_id,
    personal_tenant_ref,
    profile_index_event,
    tenant_ref,
)


PROFILE_AFFECTING_FIELDS = frozenset(
    {
        "skills",
        "projects",
        "internships",
        "education",
        "certificates",
        "competitions",
    }
)


class ResumeNotFound(LookupError):
    pass


class ParseResultNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManageResumes:
    uow_factory: Callable[[], ResumeUnitOfWork]
    files: FileUploadWorkflowPort
    extractor: ResumeInputExtractionPort
    tasks: TaskWorkflowPort
    vector_index_enabled: bool = True

    def create_text(self, actor: AccountActor, raw_text: str) -> ResumeRecord:
        require_personal_role(actor.role)
        draft = ResumeDraft(
            actor.account_id, "text", None, raw_text, "pending",
            "not_required", "direct_text",
            display_name="文本简历",
        )
        return self._add(draft)

    def create_upload(
        self,
        actor: AccountActor,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        use_ocr: bool,
    ) -> ResumeRecord:
        require_personal_role(actor.role)
        source_type = "image" if use_ocr else "file"
        asset = self.files.upload(
            actor,
            filename=filename,
            content_type=content_type,
            content=content,
            purpose=f"resume_{source_type}",
        )
        outcome = self.extractor.extract(
            asset.storage_key, asset.content_type, use_ocr=use_ocr
        )
        return self._add(
            ResumeDraft(
                actor.account_id,
                source_type,
                asset.file_id,
                outcome.text,
                "pending" if outcome.status == "completed" else "failed",
                outcome.status,
                outcome.provider,
                outcome.error_code,
                outcome.error_message,
                display_name=(filename.rsplit(".", 1)[0] or "上传简历")[:120],
                original_filename=filename[:255],
            )
        )

    def import_validated_cv(
        self,
        actor: AccountActor,
        *,
        validated_cv_snapshot_id: str,
        source_cv_version_id: str,
        raw_text: str,
        extraction_payload: FrozenJsonObject,
        normalized_payload: FrozenJsonObject,
        review_flags: tuple[FrozenJsonObject, ...],
        resume_id: str | None = None,
    ) -> ResumeRecord:
        """Idempotently create a Resume draft from a validated CV snapshot."""
        require_personal_role(actor.role)
        if extraction_payload.get("document_id") != source_cv_version_id:
            raise ResumeRuleViolation("CV extraction lineage does not match source version")
        if normalized_payload.get("document_id") != source_cv_version_id:
            raise ResumeRuleViolation("CV normalization lineage does not match source version")
        with self.uow_factory() as uow:
            existing = uow.resumes.get_by_source_cv_version(source_cv_version_id)
            if (
                existing is not None
                and existing.validated_cv_snapshot_id == validated_cv_snapshot_id
            ):
                return existing
            if existing is not None and existing.user_id != actor.account_id:
                raise ResumeRuleViolation("CV source version belongs to another user")
            if existing is None:
                resume = uow.resumes.add(
                    ResumeDraft(
                        actor.account_id,
                        "text",
                        None,
                        raw_text,
                        "completed",
                        "completed",
                        "cv_extraction_http",
                        source_cv_version_id=source_cv_version_id,
                        validated_cv_snapshot_id=validated_cv_snapshot_id,
                        display_name="智能抽取简历",
                        resume_id=resume_id,
                    )
                )
            else:
                # Keep the stable Resume identity while replacing all derived
                # data that came from the previous authoritative snapshot.
                resume = uow.resumes.update_validated_source(
                    existing.resume_id,
                    validated_cv_snapshot_id=validated_cv_snapshot_id,
                    source_cv_version_id=source_cv_version_id,
                    raw_text=raw_text,
                )
                # A profile generated from the old snapshot must not survive
                # after the parse result has been replaced.
                uow.resumes.replace_skills(resume.resume_id, ())
            draft = self._validated_parse_draft(
                resume.resume_id,
                extraction_payload=extraction_payload,
                normalized_payload=normalized_payload,
                review_flags=review_flags,
            )
            uow.resumes.save_parse_result(draft)
            uow.resumes.replace_skills(
                resume.resume_id,
                tuple(self._skill_draft(item) for item in draft.skills),
            )
            uow.resumes.save_position_classifications(
                resume.resume_id,
                normalized_payload=normalized_payload,
                source_snapshot_id=validated_cv_snapshot_id,
            )
            uow.resumes.set_parse_status(resume.resume_id, "completed")
            uow.commit()
            return resume

    def publish_profile_refresh(
        self,
        resume_id: str,
        event_type: str,
        *,
        snapshot_id: str | None = None,
        snapshot_revision: int | None = None,
        source_version: str | None = None,
    ) -> None:
        with self.uow_factory() as uow:
            record = uow.resumes.get(resume_id)
            if record is None:
                raise ResumeNotFound("Resume not found")
            self._enqueue_profile_refresh(
                uow,
                record,
                event_type,
                snapshot_id=snapshot_id,
                snapshot_revision=snapshot_revision,
                source_version=source_version,
            )
            uow.commit()

    def list_mine(self, actor: AccountActor) -> list[ResumeRecord]:
        require_personal_role(actor.role)
        with self.uow_factory() as uow:
            return uow.resumes.list_by_user(actor.account_id)

    def get(self, actor: AccountActor, resume_id: str) -> ResumeRecord:
        with self.uow_factory() as uow:
            record = uow.resumes.get(resume_id)
        if record is None:
            raise ResumeNotFound("Resume not found")
        self._authorize_read(actor, record)
        return record

    def rename(
        self, actor: AccountActor, resume_id: str, display_name: str
    ) -> ResumeRecord:
        record = self.get(actor, resume_id)
        self._authorize_write(actor, record)
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ResumeRuleViolation("Resume display name cannot be empty")
        if len(normalized_name) > 120:
            raise ResumeRuleViolation("Resume display name is too long")
        with self.uow_factory() as uow:
            updated = uow.resumes.rename(resume_id, normalized_name)
            uow.commit()
            return updated

    def delete(self, actor: AccountActor, resume_id: str) -> None:
        record = self.get(actor, resume_id)
        self._authorize_write(actor, record)
        with self.uow_factory() as uow:
            self._enqueue_profile_refresh(uow, record, "cv_profile_revoked")
            uow.resumes.delete(resume_id)
            uow.commit()

    def parse(self, actor: AccountActor, resume_id: str) -> TaskRecord:
        resume = self.get(actor, resume_id)
        self._authorize_write(actor, resume)
        if resume.source_cv_version_id is not None:
            raise ResumeRuleViolation(
                "CV-sourced Resume must be revised through ValidatedCVSnapshot"
            )
        draft = self._build_parse_draft(resume)
        with self.uow_factory() as uow:
            uow.resumes.set_parse_status(resume_id, "running")
            result = uow.resumes.save_parse_result(draft)
            uow.resumes.set_parse_status(resume_id, "completed")
            task = self.tasks.prepare_succeeded(
                actor,
                "resume_parse",
                input_payload=TaskPayload.from_mapping({"resume_id": resume_id}),
                result_payload=TaskPayload.from_mapping({
                    "resume_id": resume_id,
                    "parse_result": _parse_result_payload(result),
                }),
                result_reference=f"resume_parse_result:{result.parse_result_id}",
            )
            uow.add_task(task)
            uow.commit()
        return task

    def get_parse_result(self, actor: AccountActor, resume_id: str) -> ParseResultRecord:
        self.get(actor, resume_id)
        with self.uow_factory() as uow:
            result = uow.resumes.get_parse_result(resume_id)
        if result is None:
            raise ParseResultNotFound("Resume parse result not found")
        return result

    def update_parse_result(
        self, actor: AccountActor, resume_id: str, changes: ParseResultChanges
    ) -> ParseResultRecord:
        resume = self.get(actor, resume_id)
        self._authorize_write(actor, resume)
        if resume.source_cv_version_id is not None:
            raise ResumeRuleViolation(
                "CV-sourced Resume must be revised through ValidatedCVSnapshot"
            )
        with self.uow_factory() as uow:
            current = uow.resumes.get_parse_result(resume_id)
            if current is None:
                current_draft = (
                    self._build_parse_draft(resume)
                    if resume.raw_text.strip()
                    else self._empty_parse_draft(resume_id)
                )
            else:
                current_draft = self._draft_from_record(current)
            updated = self._apply_changes(current_draft, changes)
            result = uow.resumes.save_parse_result(updated)
            # Parsed sections are the source of the derived skill profile.
            # Invalidate both in the same transaction so matching cannot
            # observe a new parse result paired with an old profile.
            if changes.changed_fields & PROFILE_AFFECTING_FIELDS:
                uow.resumes.replace_skills(resume_id, ())
            uow.resumes.set_parse_status(resume_id, "completed")
            self._enqueue_profile_refresh(uow, resume, "cv_profile_updated")
            uow.commit()
            return result

    def confirm(self, actor: AccountActor, resume_id: str) -> ParseResultRecord:
        resume = self.get(actor, resume_id)
        self._authorize_write(actor, resume)
        if resume.source_cv_version_id is not None:
            raise ResumeRuleViolation(
                "CV-sourced Resume must be revised through ValidatedCVSnapshot"
            )
        with self.uow_factory() as uow:
            current = uow.resumes.get_parse_result(resume_id)
            if current is None:
                raise ParseResultNotFound("Resume parse result not found")
            result = uow.resumes.save_parse_result(
                replace(self._draft_from_record(current), need_review=False)
            )
            uow.resumes.set_parse_status(resume_id, "completed")
            self._enqueue_profile_refresh(uow, resume, "cv_profile_updated")
            uow.commit()
            return result

    def generate_skill_profile(
        self, actor: AccountActor, resume_id: str
    ) -> list[ResumeSkillRecord]:
        resume = self.get(actor, resume_id)
        self._authorize_write(actor, resume)
        if resume.source_cv_version_id is not None:
            raise ResumeRuleViolation(
                "CV-sourced Resume must be revised through ValidatedCVSnapshot"
            )
        with self.uow_factory() as uow:
            result = uow.resumes.get_parse_result(resume_id)
            if result is None:
                result = uow.resumes.save_parse_result(self._build_parse_draft(resume))
                uow.resumes.set_parse_status(resume_id, "completed")
            if result.need_review:
                raise ResumeRuleViolation(
                    "Resume parse result requires confirmation before skill profile generation"
                )
            skills = tuple(self._skill_draft(item) for item in result.skills)
            profile = uow.resumes.replace_skills(resume_id, skills)
            self._enqueue_profile_refresh(uow, resume, "cv_profile_updated")
            uow.commit()
            return profile

    def get_skill_profile(
        self, actor: AccountActor, resume_id: str
    ) -> list[ResumeSkillRecord]:
        self.get(actor, resume_id)
        with self.uow_factory() as uow:
            return uow.resumes.list_skills(resume_id)

    def _add(self, draft: ResumeDraft) -> ResumeRecord:
        with self.uow_factory() as uow:
            record = uow.resumes.add(draft)
            uow.commit()
            return record

    def _enqueue_profile_refresh(
        self,
        uow: ResumeUnitOfWork,
        resume: ResumeRecord,
        event_type: str,
        *,
        snapshot_id: str | None = None,
        snapshot_revision: int | None = None,
        source_version: str | None = None,
    ) -> None:
        if not self.vector_index_enabled:
            return
        personal_tenant = personal_tenant_ref(resume.user_id)
        profile_source_version = source_version
        if snapshot_id is not None and snapshot_revision is not None:
            profile_source_version = f"snapshot={snapshot_id}:{snapshot_revision}"
        uow.add_outbox(
            profile_index_event(
                vector_event_type=event_type,
                entity_type="cv",
                entity_id=resume.resume_id,
                tenant=personal_tenant,
                target_type="candidate_cv",
                snapshot_id=snapshot_id,
                snapshot_revision=snapshot_revision,
                source_version=profile_source_version,
            )
        )
        projection_event = (
            "cv_profile_revoked"
            if event_type == "cv_profile_revoked"
            else "cv_profile_updated"
        )
        for grant in uow.active_grants(resume.resume_id):
            enterprise_tenant = tenant_ref(grant.enterprise_id)
            uow.add_outbox(
                profile_index_event(
                    vector_event_type=projection_event,
                    entity_type="cv",
                    entity_id=enterprise_projection_entity_id(
                        resume.resume_id, grant.grant_id
                    ),
                    source_entity_id=resume.resume_id,
                    tenant=enterprise_tenant,
                    target_type="candidate_cv",
                    grant_id=grant.grant_id,
                    grant_version=grant.grant_version,
                    personal_tenant=personal_tenant,
                    enterprise_tenant=enterprise_tenant,
                    snapshot_id=snapshot_id,
                    snapshot_revision=snapshot_revision,
                    source_version=source_version,
                )
            )

    @staticmethod
    def _validated_parse_draft(
        resume_id: str,
        *,
        extraction_payload: FrozenJsonObject,
        normalized_payload: FrozenJsonObject,
        review_flags: tuple[FrozenJsonObject, ...],
    ) -> ParseResultDraft:
        normalized_by_source = {
            item.get("source_item_id"): item
            for item in normalized_payload.get("normalized_skills", [])
            if isinstance(item, Mapping)
        }
        extracted_skills = list(extraction_payload.get("skills", []))
        for section in ("work_experience", "project_experience"):
            for entry in extraction_payload.get(section, []):
                if not isinstance(entry, Mapping):
                    raise ResumeRuleViolation("Invalid validated CV experience entry")
                extracted_skills.extend(entry.get("tech_stack", []))
        skills = []
        for item in extracted_skills:
            if not isinstance(item, Mapping):
                raise ResumeRuleViolation("Invalid validated CV skill entry")
            normalized = normalized_by_source.get(item.get("item_id"), {})
            resolution_status = normalized.get("resolution_status")
            normalization_confidence = normalized.get("normalization_confidence")
            if resolution_status == "resolved" and (
                isinstance(normalization_confidence, bool)
                or not isinstance(normalization_confidence, (int, float))
            ):
                raise ResumeRuleViolation(
                    "Resolved validated CV skill is missing normalization confidence"
                )
            evidence = item.get("evidence") or {}
            skills.append(
                freeze_json_object(
                    {
                        "raw_skill": item.get("name"),
                        "normalized_skill_id": normalized.get("skill_id"),
                        "confidence": (
                            float(normalization_confidence)
                            if resolution_status == "resolved"
                            else 0.0
                        ),
                        "evidence": evidence.get("quote"),
                        "proficiency": item.get("proficiency"),
                    },
                    field="validated_cv_skill",
                )
            )
        return ParseResultDraft(
            resume_id,
            tuple(
                freeze_json_object(item, field="validated_cv_education")
                for item in extraction_payload.get("education", [])
            ),
            tuple(
                freeze_json_object(item, field="validated_cv_project")
                for item in extraction_payload.get("project_experience", [])
            ),
            tuple(
                freeze_json_object(item, field="validated_cv_work")
                for item in extraction_payload.get("work_experience", [])
            ),
            tuple(skills),
            tuple(
                freeze_json_object(item, field="validated_cv_certificate")
                for item in extraction_payload.get("certificates", [])
            ),
            tuple(
                freeze_json_object(item, field="validated_cv_award")
                for item in extraction_payload.get("awards", [])
            ),
            ManageResumes._cv_confidence(
                extraction_payload,
                unresolved_count=len(normalized_payload.get("unresolved_items", [])),
                review_flag_count=len(review_flags),
            ),
            bool(review_flags or normalized_payload.get("unresolved_items")),
        )

    @staticmethod
    def _cv_confidence(
        extraction_payload: FrozenJsonObject,
        *,
        unresolved_count: int,
        review_flag_count: int,
    ) -> float:
        """Score completeness without treating an empty shell as certainty."""
        score = 0.6
        score += 0.1 if extraction_payload.get("skills") else 0.0
        score += 0.05 if extraction_payload.get("education") else 0.0
        score += 0.1 if extraction_payload.get("work_experience") else 0.0
        score += 0.1 if extraction_payload.get("project_experience") else 0.0
        score += (
            0.05
            if extraction_payload.get("certificates")
            or extraction_payload.get("awards")
            else 0.0
        )
        score -= min(0.3, 0.05 * (unresolved_count + review_flag_count))
        return round(max(0.0, min(score, 1.0)), 2)

    @staticmethod
    def _authorize_read(actor: AccountActor, record: ResumeRecord) -> None:
        if record.user_id != actor.account_id and actor.role not in RESUME_READ_ROLES:
            raise PermissionDenied("No permission for this resume")

    @staticmethod
    def _authorize_write(actor: AccountActor, record: ResumeRecord) -> None:
        if record.user_id != actor.account_id and actor.role not in RESUME_WRITE_ROLES:
            raise PermissionDenied("No write permission for this resume")

    @staticmethod
    def _build_parse_draft(resume: ResumeRecord) -> ParseResultDraft:
        if not resume.raw_text.strip():
            raise ResumeRuleViolation(
                "Resume text is unavailable; edit the parse result manually or configure an input adapter"
            )
        candidates = extract_skill_candidates(resume.raw_text)
        skills = tuple(
            {
                "raw_skill": item.raw_skill,
                "normalized_skill_id": item.normalized_skill_id,
                "confidence": item.confidence,
                "evidence": item.evidence,
            }
            for item in candidates
        )
        return ParseResultDraft(
            resume.resume_id, (), (), (), skills, (), (), 0.7, True
        )

    @staticmethod
    def _empty_parse_draft(resume_id: str) -> ParseResultDraft:
        return ParseResultDraft(resume_id, (), (), (), (), (), (), 0.0, True)

    @staticmethod
    def _draft_from_record(result: ParseResultRecord) -> ParseResultDraft:
        def frozen(values: tuple[object, ...], field: str):
            return tuple(
                freeze_json_object(value, field=field)
                for value in values
            )

        return ParseResultDraft(
            result.resume_id,
            frozen(result.education, "resume_education"),
            frozen(result.projects, "resume_project"),
            frozen(result.internships, "resume_internship"),
            frozen(result.skills, "resume_skill"),
            frozen(result.certificates, "resume_certificate"),
            frozen(result.competitions, "resume_competition"),
            result.parse_confidence,
            result.need_review,
        )

    @staticmethod
    def _apply_changes(
        draft: ParseResultDraft, changes: ParseResultChanges
    ) -> ParseResultDraft:
        values = {
            field: getattr(changes, field)
            for field in changes.changed_fields
        }
        if "parse_confidence" in values and "need_review" not in values:
            confidence = values["parse_confidence"]
            if isinstance(confidence, int | float):
                values["need_review"] = confidence < 0.9
        return replace(draft, **values)

    @staticmethod
    def _skill_draft(item: object) -> ResumeSkillDraft:
        if not isinstance(item, Mapping):
            raise ResumeRuleViolation("Invalid resume skill entry")
        raw_skill = item.get("raw_skill")
        if not isinstance(raw_skill, str) or not raw_skill:
            raise ResumeRuleViolation("Invalid resume skill entry")
        skill_id = item.get("normalized_skill_id") or item.get("skill_id") or raw_skill
        confidence = item.get("confidence", 0.9)
        return ResumeSkillDraft(
            str(skill_id), raw_skill, float(confidence),
            str(item.get("evidence", "简历解析结果")),
            str(item["proficiency"]) if item.get("proficiency") is not None else None,
        )


def _parse_result_payload(result: ParseResultRecord) -> FrozenJsonObject:
    return {
        "parse_result_id": result.parse_result_id,
        "resume_id": result.resume_id,
        "education": [dict(item) for item in result.education],
        "projects": [dict(item) for item in result.projects],
        "internships": [dict(item) for item in result.internships],
        "skills": [dict(item) for item in result.skills],
        "certificates": [dict(item) for item in result.certificates],
        "competitions": [dict(item) for item in result.competitions],
        "parse_confidence": result.parse_confidence,
        "need_review": result.need_review,
    }
