from collections.abc import Mapping
from dataclasses import replace

from app.contexts.jd_lifecycle._applications.jd_support import (
    require_optional_port_result,
    require_port_result,
)
from app.contexts.jd_lifecycle._applications.jd_common import (
    _conflict,
    _ensure_can_admin,
    _ensure_can_publish,
    _ensure_can_review,
    _ensure_can_write,
    _forbidden,
    _invalid,
    _not_found,
)
from app.contexts.jd_lifecycle._ports.jd_repository import (
    Actor,
    JDDTO,
    JDExportFile,
    JDLegacyFields,
    JDParseResultCreateCommand,
    JDParseResultDTO,
    JDPublicationDTO,
    JDParseResultReviewUpdate,
    JDParseResultVersionedUpdate,
    JDParseStatusUpdate,
    JDSchemaBundle,
    JDSchemaPersistence,
    SkillCatalogMappingResult,
    SkillReviewResult,
)
from app.domain.jd import ReviewFlag
from app.domain.jd_skill_catalog import SkillCatalogGateError, require_catalog_binding
from app.domain.jd_policies import (
    JDParseEditCommand,
    JDPolicyViolation,
    ParseReviewFacts,
    evaluate_parse_edit,
    validate_parse_edit_command,
)
from app.contracts.jd.normalization_v2 import JobClassification
from app.contracts.position_taxonomy_v3 import OBSERVED_SKILL_DOMAIN_CODES


class JDReviewUseCases:
    def map_parse_position_to_catalog(
        self,
        actor: Actor,
        parse_result_id: str,
        *,
        target_position_id: str,
        career_level: str | None = None,
        leadership_scope: str | None = None,
        technology_focus_codes: list[str] | None = None,
        industry_context_codes: list[str] | None = None,
    ) -> JDParseResultDTO:
        if actor.role not in {"admin", "developer", "reviewer"}:
            raise _forbidden("No permission to review JD position mappings")
        with self._uow_factory() as uow:
            result = require_optional_port_result(
                uow.jds.get_parse_result_by_id(parse_result_id),
                JDParseResultDTO,
                operation="JDRepository.get_parse_result_by_id",
            )
            if result is None:
                raise _not_found("JD parse result not found")
            jd = self._get_accessible_jd(uow, actor, result.jd_id, write=False)
            if result.workflow_status == "published":
                raise _conflict("Published JD results are immutable; create a new JD version")
            target = uow.position_catalog_entry(target_position_id)
            if target is None:
                raise _not_found("Standard position not found")
            if not result.extraction_result or not result.normalized_result:
                raise _conflict("Versioned extraction and normalization are required for review")
            bundle = require_port_result(
                self._schema.load(
                    result.extraction_result,
                    result.normalized_result,
                    schema_version=result.schema_version,
                    normalization_schema_version=result.normalization_schema_version,
                ),
                JDSchemaBundle,
                operation="JDSchemaPort.load",
            )
            (
                position_id,
                position_code,
                position_name,
                family_code,
                family_name,
                skill_domain_codes,
                taxonomy_version,
                lifecycle_status,
            ) = target
            if lifecycle_status != "active":
                raise _conflict("position_catalog_entry_not_active")
            source_title = str(
                (bundle.normalization.job_classification or {}).get("source_title")
                or bundle.document.title
                or jd.title
            )
            previous = dict(bundle.normalization.job_classification or {})
            observed_skill_domains = sorted(
                {
                    str(item.category_code)
                    for item in bundle.normalization.items
                    if item.item_type == "skill"
                    and item.category_code in OBSERVED_SKILL_DOMAIN_CODES
                }
            )
            classification = JobClassification.model_validate(
                {
                    "schema_version": "job-position-classification.v3",
                    "taxonomy_version": taxonomy_version,
                    "source_title": source_title,
                    "position_id": position_id,
                    "position_code": position_code,
                    "position_name": position_name,
                    "family_code": family_code,
                    "family_name": family_name,
                    "candidate_positions": previous.get("candidate_positions", []),
                    "career_level": (
                        career_level if career_level is not None else previous.get("career_level")
                    ),
                    "leadership_scope": (
                        leadership_scope
                        if leadership_scope is not None
                        else previous.get("leadership_scope")
                    ),
                    "technology_focus_codes": (
                        technology_focus_codes
                        if technology_focus_codes is not None
                        else previous.get("technology_focus_codes", [])
                    ),
                    "industry_context_codes": (
                        industry_context_codes
                        if industry_context_codes is not None
                        else previous.get("industry_context_codes", [])
                    ),
                    "observed_skill_domain_codes": previous.get(
                        "observed_skill_domain_codes", observed_skill_domains
                    )
                    or observed_skill_domains,
                    "confidence": 1.0,
                    "classification_status": "manually_confirmed",
                    "review_reason_codes": [],
                    "evidence_refs": previous.get("evidence_refs")
                    or [f"manual-review:{parse_result_id}"],
                    "classification_policy_version": previous.get(
                        "classification_policy_version", "position-classifier.v3.0"
                    ),
                }
            ).model_dump(mode="json")
            position_flag_codes = {
                "audit_batch_unresolved_position",
                "source_job_classification_unresolved",
                "job_classification_unresolved",
            }
            flags = tuple(
                flag
                for flag in bundle.normalization.review_flags
                if not (
                    flag.flag_type in {"position", "job_title", "job_classification"}
                    or flag.code in position_flag_codes
                )
            )
            updated_bundle = type(bundle)(
                document=bundle.document,
                normalization=replace(
                    bundle.normalization,
                    job_classification=classification,
                    review_flags=flags,
                ),
            )
            stored = require_port_result(
                self._schema.persist(updated_bundle),
                JDSchemaPersistence,
                operation="JDSchemaPort.persist",
            )
            legacy = require_port_result(
                self._schema.legacy(updated_bundle, jd.title),
                JDLegacyFields,
                operation="JDSchemaPort.legacy",
            )
            updated = require_port_result(
                uow.jds.update_parse_result(
                    result.id,
                    JDParseResultVersionedUpdate(
                        legacy=legacy,
                        extraction_result=stored.extraction_payload,
                        normalized_result=stored.normalization_payload,
                        schema_version=stored.schema_version,
                        normalization_schema_version=stored.normalization_schema_version,
                        need_review=True,
                        workflow_status="draft",
                    ),
                ),
                JDParseResultDTO,
                operation="JDRepository.update_parse_result",
            )
            uow.stage_validation_for_parse_result(updated.id)
            uow.commit()
            return updated

    def map_parse_skill_to_catalog(
        self,
        actor: Actor,
        parse_result_id: str,
        *,
        source_name: str,
        target_skill_id: str,
        requirement_id: str | None = None,
    ) -> SkillCatalogMappingResult:
        if actor.role not in {"admin", "developer", "reviewer"}:
            raise _forbidden("No permission to review JD skill mappings")
        with self._uow_factory() as uow:
            result = require_optional_port_result(
                uow.jds.get_parse_result_by_id(parse_result_id),
                JDParseResultDTO,
                operation="JDRepository.get_parse_result_by_id",
            )
            if result is None:
                raise _not_found("JD parse result not found")
            jd = self._get_accessible_jd(uow, actor, result.jd_id, write=False)
            if result.workflow_status == "published":
                raise _conflict("Published JD results are immutable; create a new JD version")
            target = uow.skills.get(target_skill_id)
            if target is None:
                raise _not_found("skill_catalog_snapshot_missing")
            catalog_skills, _ = uow.catalog_entries()
            try:
                catalog_target = require_catalog_binding(
                    resolution_status="resolved",
                    skill_id=target_skill_id,
                    canonical_name=target.skill_name,
                    skills=catalog_skills,
                )
            except SkillCatalogGateError as exc:
                raise _conflict(exc.code) from exc
            if not result.extraction_result or not result.normalized_result:
                raise _conflict("Versioned extraction and normalization are required for review")
            bundle = require_port_result(
                self._schema.load(
                    result.extraction_result,
                    result.normalized_result,
                    schema_version=result.schema_version,
                    normalization_schema_version=(result.normalization_schema_version),
                ),
                JDSchemaBundle,
                operation="JDSchemaPort.load",
            )
            matched = [
                item
                for item in bundle.normalization.items
                if item.item_type == "skill"
                and item.source_value == source_name
                and (
                    requirement_id is None
                    or item.raw_payload.get("requirement_id") == requirement_id
                )
            ]
            if not matched:
                raise _not_found("Skill item not found in JD parse result")
            if len(matched) > 1:
                raise _conflict("skill_catalog_conflict")
            selected = matched[0]
            raw_payload = dict(selected.raw_payload)
            raw_payload.setdefault("source_skill_id", selected.skill_id)
            raw_payload.setdefault("source_canonical_name", selected.canonical_name)
            raw_payload.setdefault("source_category_code", selected.category_code)
            raw_payload.setdefault("source_subcategory_code", selected.subcategory_code)
            raw_payload.setdefault("source_resolution_status", selected.resolution_status)
            raw_payload["resolution_source"] = "manual_review"
            updated_item = replace(
                selected,
                resolution_status="resolved",
                skill_id=catalog_target.skill_id,
                canonical_name=catalog_target.canonical_name,
                category_code=catalog_target.category_code,
                subcategory_code=None,
                raw_payload=raw_payload,
            )

            catalog_codes = {
                "skill_catalog_unresolved",
                "skill_catalog_conflict",
                "skill_catalog_snapshot_missing",
            }

            def matching_catalog_flag(flag: ReviewFlag) -> bool:
                if flag.code not in catalog_codes or flag.source_value != source_name:
                    return False
                details = flag.raw_payload.get("details")
                return requirement_id is None or (
                    isinstance(details, Mapping) and details.get("requirement_id") == requirement_id
                )

            flags = tuple(
                flag
                for flag in bundle.normalization.review_flags
                if not matching_catalog_flag(flag)
            )
            closed = len(bundle.normalization.review_flags) - len(flags)
            if closed == 0:
                raise _conflict("skill_catalog_mapping_not_pending")
            items = tuple(
                updated_item if item is selected else item for item in bundle.normalization.items
            )
            updated_bundle = type(bundle)(
                document=bundle.document,
                normalization=replace(
                    bundle.normalization,
                    items=items,
                    review_flags=flags,
                ),
            )
            stored = require_port_result(
                self._schema.persist(updated_bundle),
                JDSchemaPersistence,
                operation="JDSchemaPort.persist",
            )
            legacy = require_port_result(
                self._schema.legacy(updated_bundle, jd.title),
                JDLegacyFields,
                operation="JDSchemaPort.legacy",
            )
            remaining_blocking = any(flag.severity == "blocking" for flag in flags)
            updated = require_port_result(
                uow.jds.update_parse_result(
                    result.id,
                    JDParseResultVersionedUpdate(
                        legacy=legacy,
                        extraction_result=stored.extraction_payload,
                        normalized_result=stored.normalization_payload,
                        schema_version=stored.schema_version,
                        normalization_schema_version=(stored.normalization_schema_version),
                        need_review=True,
                        workflow_status="draft",
                    ),
                ),
                JDParseResultDTO,
                operation="JDRepository.update_parse_result",
            )
            uow.stage_validation_for_parse_result(updated.id)
            uow.commit()
            return SkillCatalogMappingResult(
                jd.id,
                result.id,
                source_name,
                requirement_id,
                catalog_target.skill_id,
                catalog_target.canonical_name,
                "resolved",
                closed,
                "blocking" if remaining_blocking else "pending_review",
            )

    def exclude_parse_skill_from_downstream(
        self,
        actor: Actor,
        parse_result_id: str,
        *,
        source_name: str,
        requirement_id: str | None,
        reason: str,
    ) -> JDParseResultDTO:
        if actor.role not in {"admin", "developer", "reviewer"}:
            raise _forbidden("No permission to review JD skill mappings")
        if not reason.strip():
            raise _invalid("Exclusion reason is required")
        with self._uow_factory() as uow:
            result = require_optional_port_result(
                uow.jds.get_parse_result_by_id(parse_result_id),
                JDParseResultDTO,
                operation="JDRepository.get_parse_result_by_id",
            )
            if result is None:
                raise _not_found("JD parse result not found")
            jd = self._get_accessible_jd(uow, actor, result.jd_id, write=False)
            if result.workflow_status == "published":
                raise _conflict("Published JD results are immutable; create a new JD version")
            if not result.extraction_result or not result.normalized_result:
                raise _conflict("Versioned extraction and normalization are required for review")
            bundle = require_port_result(
                self._schema.load(
                    result.extraction_result,
                    result.normalized_result,
                    schema_version=result.schema_version,
                    normalization_schema_version=result.normalization_schema_version,
                ),
                JDSchemaBundle,
                operation="JDSchemaPort.load",
            )
            matched = [
                item
                for item in bundle.normalization.items
                if item.item_type == "skill"
                and item.source_value == source_name
                and (
                    requirement_id is None
                    or item.raw_payload.get("requirement_id") == requirement_id
                )
            ]
            if not matched:
                raise _not_found("Skill item not found in JD parse result")
            if len(matched) > 1:
                raise _conflict("skill_catalog_conflict")
            selected = matched[0]
            raw_payload = dict(selected.raw_payload)
            raw_payload["resolution_source"] = "manual_review"
            raw_payload["exclusion_reason"] = reason.strip()
            updated_item = replace(
                selected,
                resolution_status="rejected",
                skill_id=None,
                canonical_name=None,
                category_code=None,
                subcategory_code=None,
                raw_payload=raw_payload,
            )
            catalog_codes = {
                "skill_catalog_unresolved",
                "skill_catalog_conflict",
                "skill_catalog_snapshot_missing",
            }

            def matching_catalog_flag(flag: ReviewFlag) -> bool:
                if flag.code not in catalog_codes or flag.source_value != source_name:
                    return False
                details = flag.raw_payload.get("details")
                return requirement_id is None or (
                    isinstance(details, Mapping) and details.get("requirement_id") == requirement_id
                )

            flags = tuple(
                flag
                for flag in bundle.normalization.review_flags
                if not matching_catalog_flag(flag)
            )
            updated_bundle = type(bundle)(
                document=bundle.document,
                normalization=replace(
                    bundle.normalization,
                    items=tuple(
                        updated_item if item is selected else item
                        for item in bundle.normalization.items
                    ),
                    review_flags=flags,
                ),
            )
            stored = require_port_result(
                self._schema.persist(updated_bundle),
                JDSchemaPersistence,
                operation="JDSchemaPort.persist",
            )
            legacy = require_port_result(
                self._schema.legacy(updated_bundle, jd.title),
                JDLegacyFields,
                operation="JDSchemaPort.legacy",
            )
            updated = require_port_result(
                uow.jds.update_parse_result(
                    result.id,
                    JDParseResultVersionedUpdate(
                        legacy=legacy,
                        extraction_result=stored.extraction_payload,
                        normalized_result=stored.normalization_payload,
                        schema_version=stored.schema_version,
                        normalization_schema_version=stored.normalization_schema_version,
                        need_review=True,
                        workflow_status="draft",
                    ),
                ),
                JDParseResultDTO,
                operation="JDRepository.update_parse_result",
            )
            uow.stage_validation_for_parse_result(updated.id)
            uow.commit()
            return updated

    def update_parse_result(
        self, actor: Actor, jd_id: str, command: JDParseEditCommand
    ) -> JDParseResultDTO:
        _ensure_can_write(actor)
        try:
            validate_parse_edit_command(command)
        except JDPolicyViolation as exc:
            raise _invalid(str(exc)) from exc
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
            result = require_optional_port_result(
                uow.jds.get_parse_result(jd.id),
                JDParseResultDTO,
                operation="JDRepository.get_parse_result",
            )
            if result is None:
                if command.changes_versioned_result:
                    if command.extraction_result is None:
                        raise _invalid("extraction_result is required")
                    try:
                        bundle = require_port_result(
                            self._schema.edit(
                                command.extraction_result,
                                command.normalized_result,
                            ),
                            JDSchemaBundle,
                            operation="JDSchemaPort.edit",
                        )
                        if bundle.document.document_id != jd.id:
                            raise ValueError("extraction_result.document_id must match JD")
                        bundle = self._resolve_schema_skills(uow, bundle)
                        stored = require_port_result(
                            self._schema.persist(bundle),
                            JDSchemaPersistence,
                            operation="JDSchemaPort.persist",
                        )
                    except ValueError as exc:
                        if "Only exact evidence" in str(exc):
                            raise _conflict(str(exc)) from exc
                        raise _invalid(str(exc)) from exc
                    result = require_port_result(
                        uow.jds.create_parse_result(
                            JDParseResultCreateCommand(
                                jd_id=jd.id,
                                legacy=require_port_result(
                                    self._schema.legacy(bundle, jd.title),
                                    JDLegacyFields,
                                    operation="JDSchemaPort.legacy",
                                ),
                                business_scenarios=(),
                                parse_confidence=0.0,
                                need_review=True,
                                extraction_result=stored.extraction_payload,
                                normalized_result=stored.normalization_payload,
                                schema_version=stored.schema_version,
                                normalization_schema_version=(stored.normalization_schema_version),
                                workflow_status="draft",
                            )
                        ),
                        JDParseResultDTO,
                        operation="JDRepository.create_parse_result",
                    )
                    uow.jds.flush()
                    command = JDParseEditCommand(changed_fields=frozenset())
                elif jd.raw_text.strip():
                    raise _conflict("Parse the JD with an explicit extraction_mode first")
                else:
                    result = require_port_result(
                        uow.jds.create_parse_result(
                            JDParseResultCreateCommand(
                                jd_id=jd.id,
                                legacy=JDLegacyFields(
                                    position_title=jd.title,
                                    responsibilities=(),
                                    required_skills=(),
                                    bonus_skills=(),
                                    education=None,
                                    experience=None,
                                    industry=None,
                                    tools=(),
                                ),
                                business_scenarios=(),
                                parse_confidence=0.0,
                                need_review=True,
                            )
                        ),
                        JDParseResultDTO,
                        operation="JDRepository.create_parse_result",
                    )
                    uow.jds.flush()
                    uow.review_tasks.ensure_active(
                        result.id,
                        reason="JD parse result requires review.",
                    )
            elif result.workflow_status == "published":
                raise _conflict("Published JD results are immutable; create a new JD version")
            try:
                decision = evaluate_parse_edit(
                    ParseReviewFacts(result.need_review, result.workflow_status),
                    command,
                )
            except JDPolicyViolation as exc:
                raise _invalid(str(exc)) from exc
            update = (
                self._versioned_parse_update(uow, jd, result, command)
                if command.changes_versioned_result
                else JDParseResultReviewUpdate(
                    parse_confidence=(
                        command.parse_confidence
                        if "parse_confidence" in command.changed_fields
                        else None
                    ),
                    need_review=decision.need_review,
                    workflow_status=decision.workflow_status,
                )
            )
            result = require_port_result(
                uow.jds.update_parse_result(result.id, update),
                JDParseResultDTO,
                operation="JDRepository.update_parse_result",
            )
            if result.workflow_status == "draft" and result.need_review:
                uow.review_tasks.ensure_active(
                    result.id,
                    reason="JD parse result changed and requires review.",
                )
            uow.stage_validation_for_parse_result(result.id)
            require_port_result(
                uow.jds.update_jd(jd.id, JDParseStatusUpdate("completed")),
                JDDTO,
                operation="JDRepository.update_jd",
            )
            uow.commit()
            return result

    def confirm_parse_result(self, actor: Actor, jd_id: str) -> JDParseResultDTO:
        _ensure_can_review(actor)
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=False)
            result = self._get_parse_result(uow, jd.id)
            if result.workflow_status == "published":
                publication = require_optional_port_result(
                    uow.publications.get_by_parse_result(result.id),
                    JDPublicationDTO,
                    operation="JDPublicationRepository.get_by_parse_result",
                )
                if publication is None:
                    raise _conflict("Published JD result has no publication snapshot")
                return result
            self._assert_publishable_result(uow, result, for_confirmation=True)
            try:
                uow.review_tasks.ensure_active(
                    result.id,
                    reason="Legacy confirm adapted to ReviewTask approval.",
                )
                uow.review_tasks.approve_active(
                    result.id,
                    actor_id=actor.id,
                    actor_role=actor.role,
                    comment="Approved through legacy JD confirm API.",
                )
            except (LookupError, RuntimeError, ValueError) as exc:
                raise _conflict(str(exc)) from exc
            result = self._get_parse_result(uow, jd.id)
            require_port_result(
                uow.jds.update_jd(jd.id, JDParseStatusUpdate("completed")),
                JDDTO,
                operation="JDRepository.update_jd",
            )
            uow.commit()
            return result

    def publish_parse_result(self, actor: Actor, jd_id: str) -> JDParseResultDTO:
        _ensure_can_publish(actor)
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
            result = self._get_parse_result(uow, jd.id)
            result = self._publish_in_uow(uow, actor, result)
            uow.commit()
            return result

    def publish_parse_result_by_id(self, actor: Actor, parse_result_id: str) -> JDPublicationDTO:
        _ensure_can_publish(actor)
        with self._uow_factory() as uow:
            result = require_optional_port_result(
                uow.jds.get_parse_result_by_id(parse_result_id),
                JDParseResultDTO,
                operation="JDRepository.get_parse_result_by_id",
            )
            if result is None:
                raise _not_found("JD parse result not found")
            self._get_accessible_jd(uow, actor, result.jd_id, write=True)
            self._publish_in_uow(uow, actor, result)
            publication = require_optional_port_result(
                uow.publications.get_by_parse_result(result.id),
                JDPublicationDTO,
                operation="JDPublicationRepository.get_by_parse_result",
            )
            if publication is None:
                raise _conflict("JD publication snapshot was not created")
            uow.commit()
            return publication

    def get_publication(self, actor: Actor, parse_result_id: str) -> JDPublicationDTO:
        with self._uow_factory() as uow:
            result = require_optional_port_result(
                uow.jds.get_parse_result_by_id(parse_result_id),
                JDParseResultDTO,
                operation="JDRepository.get_parse_result_by_id",
            )
            if result is None:
                raise _not_found("JD parse result not found")
            self._get_accessible_jd(uow, actor, result.jd_id, write=False)
            publication = require_optional_port_result(
                uow.publications.get_by_parse_result(result.id),
                JDPublicationDTO,
                operation="JDPublicationRepository.get_by_parse_result",
            )
            if publication is None:
                raise _not_found("JD publication not found")
            return publication

    def _publish_in_uow(self, uow, actor: Actor, result: JDParseResultDTO) -> JDParseResultDTO:
        existing = require_optional_port_result(
            uow.publications.get_by_parse_result(result.id),
            JDPublicationDTO,
            operation="JDPublicationRepository.get_by_parse_result",
        )
        if existing is not None:
            if result.workflow_status != "published":
                raise _conflict("Publication snapshot exists for a non-published JD result")
            return result
        self._assert_publishable_result(uow, result, for_confirmation=False)
        if result.workflow_status != "reviewed" or result.need_review:
            raise _conflict("JD result must be reviewed before publication")
        validation_lineage = {
            "state": "absent",
            "absent_reason": "validation_not_enforced",
        }
        if self._data_validation_mode == "enforce":
            decision = uow.validation_publication_gate.evaluate(
                jd_id=result.jd_id,
                parse_result_id=result.id,
            )
            if decision.decision != "allow":
                raise _conflict(decision.code)
            validation_lineage = {
                "state": "present",
                "data_validation_task_id": decision.validation_task_id,
                "validation_report_id": decision.validation_report_id,
                "validated_bundle_snapshot_id": decision.validation_snapshot_id,
                "governance_review_task_id": decision.governance_review_task_id,
                "validation_conclusion": decision.conclusion,
                "validation_policy_version": decision.policy_binding_version,
                "bundle_id": decision.bundle_id,
            }
        published = require_port_result(
            uow.jds.update_parse_result(
                result.id, JDParseResultReviewUpdate(workflow_status="published")
            ),
            JDParseResultDTO,
            operation="JDRepository.update_parse_result",
        )
        uow.jds.flush()
        require_port_result(
            uow.publications.add(
                result.id,
                published_by=actor.id,
                published_by_role=actor.role,
                validation_lineage=validation_lineage,
            ),
            JDPublicationDTO,
            operation="JDPublicationRepository.add",
        )
        return published

    def export_parse_result(self, actor: Actor, jd_id: str) -> JDExportFile:
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=False)
            result = self._get_parse_result(uow, jd.id)
            if not result.extraction_result or not result.normalized_result:
                raise _conflict("Versioned extraction and normalization are required for export")
            bundle = require_port_result(
                self._schema.load(
                    result.extraction_result,
                    result.normalized_result,
                    schema_version=result.schema_version,
                    normalization_schema_version=result.normalization_schema_version,
                ),
                JDSchemaBundle,
                operation="JDSchemaPort.load",
            )
            return require_port_result(
                self._exporter.export(bundle.document, bundle.normalization),
                JDExportFile,
                operation="JDExportPort.export",
            )

    def mark_parse_skill_abnormal(
        self,
        actor: Actor,
        jd_id: str,
        skill_id: str,
        *,
        abnormal: bool,
        reason: str | None,
    ) -> SkillReviewResult:
        _ensure_can_admin(actor)
        if not isinstance(abnormal, bool):
            raise _invalid("abnormal must be a boolean")
        if reason is not None and not isinstance(reason, str):
            raise _invalid("reason must be a string")
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
            result = self._get_parse_result(uow, jd.id)
            if result.workflow_status == "published":
                raise _conflict("Published JD results are immutable; create a new JD version")
            if not result.extraction_result or not result.normalized_result:
                raise _conflict("Versioned extraction and normalization are required for review")
            bundle = require_port_result(
                self._schema.load(
                    result.extraction_result,
                    result.normalized_result,
                    schema_version=result.schema_version,
                    normalization_schema_version=result.normalization_schema_version,
                ),
                JDSchemaBundle,
                operation="JDSchemaPort.load",
            )
            matched = next(
                (
                    item
                    for item in bundle.normalization.items
                    if item.skill_id == skill_id or item.source_value == skill_id
                ),
                None,
            )
            if matched is None:
                raise _not_found("Skill item not found in JD parse result")
            flags = tuple(
                flag
                for flag in bundle.normalization.review_flags
                if not (flag.code == "skill_abnormal" and flag.source_value == matched.source_value)
            )
            if abnormal:
                flags += (
                    ReviewFlag(
                        flag_type="skill",
                        source_value=matched.source_value,
                        reason=reason or "Skill marked abnormal by reviewer",
                        severity="blocking",
                        source="manual_review",
                        code="skill_abnormal",
                    ),
                )
            updated_bundle = type(bundle)(
                document=bundle.document,
                normalization=replace(bundle.normalization, review_flags=flags),
            )
            stored = require_port_result(
                self._schema.persist(updated_bundle),
                JDSchemaPersistence,
                operation="JDSchemaPort.persist",
            )
            legacy = require_port_result(
                self._schema.legacy(updated_bundle, jd.title),
                JDLegacyFields,
                operation="JDSchemaPort.legacy",
            )
            result = require_port_result(
                uow.jds.update_parse_result(
                    result.id,
                    JDParseResultVersionedUpdate(
                        legacy=legacy,
                        extraction_result=stored.extraction_payload,
                        normalized_result=stored.normalization_payload,
                        schema_version=stored.schema_version,
                        normalization_schema_version=stored.normalization_schema_version,
                        need_review=any(flag.severity == "blocking" for flag in flags),
                        workflow_status="draft",
                    ),
                ),
                JDParseResultDTO,
                operation="JDRepository.update_parse_result",
            )
            uow.commit()
            return SkillReviewResult(
                jd.id,
                result.id,
                skill_id,
                matched.source_value,
                (matched.skill_id),
                abnormal,
                reason if abnormal else None,
                "needs_review" if abnormal else "cleared",
                "domain_review_flag_persisted_via_contract",
            )
