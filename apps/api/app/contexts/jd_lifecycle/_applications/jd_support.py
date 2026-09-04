import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import TypeVar

from app.contexts.extraction_tasks.drafts import map_bundle_to_framework_draft
from app.contexts.extraction_tasks.ports import JDExtractionProvider
from app.contexts.extraction_tasks.ports import ExtractionProviderError
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_bundle import (
    parse_extracted_jd_bundle,
    validate_bundle_matches_envelope,
)

from app.contexts.jd_lifecycle._applications.jd_common import (
    JD_ADMIN_ROLES,
    _conflict,
    _ensure_can_read,
    _ensure_can_write,
    _forbidden,
    _invalid,
    _not_found,
)


from app.domain.jd_policies import (
    DuplicateCandidateFacts,
    DuplicateFacts,
    InflationFacts,
    JDParseEditCommand,
    ParseFacts,
    evaluate_duplicate,
    evaluate_inflation,
    evaluate_parse,
)
from app.contexts.jd_lifecycle._ports.jd_repository import (
    AbnormalSkill,
    Actor,
    DuplicateCheckResult,
    InflationCheckResult,
    JDDTO,
    JDCopyRiskUpdate,
    JDInflationUpdate,
    JDParseResultDTO,
    JDParseResultCreateCommand,
    JDParseResultVersionedUpdate,
    JDParseStatusUpdate,
    JDLegacyFields,
    JDSchemaBundle,
    JDSchemaPersistence,
    JDUoW,
    SimilarJD,
)
from app.domain.jd_skill_catalog import (
    SkillCatalogGateError,
    require_catalog_binding,
    resolve_catalog_skill,
)
from app.domain.jd import ReviewFlag


T = TypeVar("T")


class PortContractError(RuntimeError):
    """A JD Port implementation returned a value outside its declared contract."""


def require_port_result(
    value: T, expected_type: type[T], *, operation: str
) -> T:
    if not isinstance(value, expected_type):
        raise PortContractError(
            f"{operation} expected {expected_type.__name__}, "
            f"got {type(value).__name__}"
        )
    return value


def require_optional_port_result(
    value: T | None, expected_type: type[T], *, operation: str
) -> T | None:
    if value is None:
        return None
    return require_port_result(value, expected_type, operation=operation)


def require_port_list(
    value: list[T] | tuple[T, ...], expected_item_type: type[T], *, operation: str
) -> list[T]:
    if not isinstance(value, list):
        raise PortContractError(
            f"{operation} expected list[{expected_item_type.__name__}], "
            f"got {type(value).__name__}"
        )
    for index, item in enumerate(value):
        if not isinstance(item, expected_item_type):
            raise PortContractError(
                f"{operation}[{index}] expected {expected_item_type.__name__}, "
                f"got {type(item).__name__}"
            )
    return value


def _parse_facts_from_bundle(
    bundle: object,
    normalization_payload: Mapping[str, object] | None = None,
) -> ParseFacts:
    extraction = bundle.extraction_result
    normalization = bundle.normalized_result
    required_field_coverage = sum(
        (
            extraction.job_title is not None,
            bool(extraction.responsibilities),
            bool(extraction.requirements),
        )
    ) / 3

    evidence_items = []
    if extraction.job_title is not None:
        evidence_items.append(extraction.job_title.evidence)
    for item in (
        *extraction.responsibilities,
        *extraction.requirements,
        *extraction.company_facts,
        *extraction.employment_facts,
    ):
        evidence_items.append(item.evidence)
    exact_evidence_ratio = (
        sum(item.alignment == "exact" for item in evidence_items)
        / len(evidence_items)
        if evidence_items
        else 0.0
    )

    extracted_skill_names = {
        item.name.casefold()
        for requirement in extraction.requirements
        if requirement.kind == "skill"
        for item in requirement.items
    }
    if normalization_payload is None:
        normalized_skills = [
            skill
            for requirement in normalization.normalized_requirements
            for skill in requirement.normalized_skills
        ]
        resolved_skill_names = {
            skill.source_name.casefold()
            for skill in normalized_skills
            if skill.resolution_status in {"resolved", "manually_confirmed"}
        }
        unresolved_count = len(normalization.unresolved_items) + len(
            bundle.review_flags
        )
        normalization_blocking = False
        classification = normalization.job_classification
        classification_evidence_refs = (
            classification.evidence_refs if classification is not None else ()
        )
        industry_context_codes = (
            classification.industry_context_codes
            if classification is not None
            else ()
        )
    else:
        normalized_skills = [
            item
            for item in normalization_payload.get("normalized_requirements", ())
            if isinstance(item, Mapping)
        ]
        resolved_skill_names = {
            str(item.get("source_name", "")).casefold()
            for item in normalized_skills
            if item.get("resolution_status") in {"resolved", "manually_confirmed"}
        }
        unresolved_items = normalization_payload.get("unresolved_items", ())
        unresolved_count = (
            len(unresolved_items)
            if isinstance(unresolved_items, (list, tuple))
            else 1
        )
        normalization_blocking = bool(
            isinstance(unresolved_items, (list, tuple))
            and any(
                isinstance(item, Mapping)
                and str(item.get("severity", "")).lower() == "blocking"
                for item in unresolved_items
            )
        )
        classification_value = normalization_payload.get("job_classification")
        classification = (
            classification_value
            if isinstance(classification_value, Mapping)
            else {}
        )
        classification_evidence_refs = classification.get("evidence_refs", ())
        industry_context_codes = classification.get("industry_context_codes", ())
    normalization_coverage = (
        len(extracted_skill_names & resolved_skill_names)
        / len(extracted_skill_names)
        if extracted_skill_names
        else 1.0
    )

    unresolved_ratio = unresolved_count / max(
        1, len(normalized_skills) + unresolved_count
    )
    provider_requires_review = bool(
        getattr(bundle, "need_review", False)
        or bundle.review_flags
        or normalization_blocking
    )
    schema_provider_valid = bool(
        getattr(bundle, "schema_version", "")
        in {"extracted-jd-bundle-v1", "extracted-jd-bundle-v2"}
        and extraction.schema_version == "v2"
        and normalization.schema_version == "v2"
        and str(getattr(bundle, "extraction_provider", "")).strip()
    )

    scenarios: tuple[str, ...] = ()
    if classification_evidence_refs and isinstance(
        industry_context_codes, (list, tuple)
    ):
        scenarios = tuple(
            dict.fromkeys(str(value) for value in industry_context_codes)
        )
    return ParseFacts(
        title=(extraction.job_title.text if extraction.job_title is not None else ""),
        raw_text=str(getattr(bundle, "cleaned_text", "")),
        required_field_coverage=required_field_coverage,
        exact_evidence_ratio=exact_evidence_ratio,
        unresolved_ratio=unresolved_ratio,
        normalization_coverage=normalization_coverage,
        schema_provider_valid=schema_provider_valid,
        provider_requires_review=provider_requires_review,
        business_scenarios=scenarios,
    )


def inflation_facts_from_result(
    jd: JDDTO, result: JDParseResultDTO
) -> InflationFacts:
    normalized = result.normalized_result or {}
    classification_value = normalized.get("job_classification")
    classification = (
        classification_value
        if isinstance(classification_value, Mapping)
        else {}
    )
    career_level_value = classification.get("career_level")
    career_level = (
        str(career_level_value) if career_level_value is not None else None
    )
    leadership_scope = classification.get("leadership_scope")
    leadership_signals = (
        (f"leadership_scope:{leadership_scope}",)
        if leadership_scope not in {None, "none"}
        else ()
    )

    minimum_years: list[float] = []
    extraction = result.extraction_result or {}
    requirements = extraction.get("requirements", ())
    if isinstance(requirements, (list, tuple)):
        for requirement in requirements:
            if not isinstance(requirement, Mapping):
                continue
            value = requirement.get("minimum_years")
            if (
                requirement.get("kind") == "experience"
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
            ):
                minimum_years.append(float(value))
    if not minimum_years and result.experience:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:[-~至]\s*\d+(?:\.\d+)?)?\s*年", result.experience)
        if match:
            minimum_years.append(float(match.group(1)))

    return InflationFacts(
        title=jd.title or result.position_title or "",
        required_skill_names=tuple(
            skill.raw_skill for skill in (result.required_skills or [])
        ),
        career_level=career_level,
        min_experience_years=min(minimum_years, default=None),
        responsibilities=tuple(result.responsibilities or ()),
        leadership_signals=leadership_signals,
    )


def _normalized_skill_ids(
    result: JDParseResultDTO | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if result is None:
        return (), ()
    required = tuple(
        skill.normalized_skill_id
        for skill in result.required_skills
        if skill.normalized_skill_id
    )
    bonus = tuple(
        skill.normalized_skill_id
        for skill in result.bonus_skills
        if skill.normalized_skill_id
    )
    return required, bonus


class JDSupportUseCases:
    _extraction_providers: dict[str, JDExtractionProvider] = {}

    def _resolve_enterprise_id(
        self, uow: JDUoW, actor: Actor, enterprise_id: str | None
    ) -> str | None:
        if actor.role == "enterprise_user":
            enterprise_ids = uow.jds.owned_enterprise_ids(actor.id)
            if not enterprise_ids:
                raise _forbidden("Create an enterprise profile before creating JD data")
            if enterprise_id and enterprise_id not in enterprise_ids:
                raise _forbidden("No permission to create JD data for this enterprise")
            return enterprise_id or enterprise_ids[0]
        return enterprise_id

    def _get_accessible_jd(
        self, uow: JDUoW, actor: Actor, jd_id: str, *, write: bool
    ) -> JDDTO:
        _ensure_can_write(actor) if write else _ensure_can_read(actor)
        jd = require_optional_port_result(
            uow.jds.get_jd(jd_id), JDDTO, operation="JDRepository.get_jd"
        )
        if jd is None:
            raise _not_found("JD not found")
        if actor.role in JD_ADMIN_ROLES or (not write and actor.role == "reviewer"):
            return jd
        if actor.role == "enterprise_user" and jd.enterprise_id in uow.jds.owned_enterprise_ids(actor.id):
            return jd
        raise _forbidden("No permission to access this JD")

    def _get_parse_result(self, uow: JDUoW, jd_id: str) -> JDParseResultDTO:
        result = require_optional_port_result(
            uow.jds.get_parse_result(jd_id),
            JDParseResultDTO,
            operation="JDRepository.get_parse_result",
        )
        if result is None:
            raise _not_found("JD parse result not found")
        return result

    def _parse_jd(
        self, uow: JDUoW, actor: Actor, jd: JDDTO, extraction_mode: str
    ) -> JDParseResultDTO:
        _ensure_can_write(actor)
        if not (jd.cleaned_text or jd.raw_text).strip():
            raise _conflict("JD text is unavailable; edit the raw text or parse result manually")
        require_port_result(
            uow.jds.update_jd(jd.id, JDParseStatusUpdate("running")),
            JDDTO,
            operation="JDRepository.update_jd",
        )
        uow.jds.flush()
        provider = self._extraction_providers.get(extraction_mode)
        if provider is None:
            raise _invalid("extraction_mode must explicitly select llm or rule")
        try:
            stored, legacy, provider_bundle, catalog_identity_metadata = (
                self._extract_through_provider(uow, jd, provider)
            )
        except ExtractionProviderError as exc:
            raise _conflict(f"{exc.code}: extraction failed") from exc
        decision = evaluate_parse(
            _parse_facts_from_bundle(
                provider_bundle,
                stored.normalization_payload,
            )
        )
        is_rule = extraction_mode == "rule"
        provider_execution = getattr(provider_bundle, "execution", None)
        execution_metadata = {
            **(
                provider_execution.model_dump(mode="json")
                if provider_execution is not None
                else {}
            ),
            **catalog_identity_metadata,
            "parse_quality": {
                "score": decision.parse_quality_score,
                "level": decision.quality_level,
                "components": dict(decision.quality_components),
            },
        }
        result = require_port_result(uow.jds.upsert_parse_result(
            jd.id,
            JDParseResultCreateCommand(
                jd_id=jd.id,
                legacy=legacy,
                business_scenarios=decision.business_scenarios,
                parse_confidence=decision.parse_quality_score,
                need_review=decision.need_review,
                extraction_result=stored.extraction_payload,
                normalized_result=stored.normalization_payload,
                execution_metadata=execution_metadata,
                schema_version=stored.schema_version,
                normalization_schema_version=stored.normalization_schema_version,
                workflow_status="draft",
            ),
        ), JDParseResultDTO, operation="JDRepository.upsert_parse_result")
        require_port_result(
            uow.jds.update_jd(jd.id, JDParseStatusUpdate("completed")),
            JDDTO,
            operation="JDRepository.update_jd",
        )
        if result.need_review:
            uow.review_tasks.ensure_active(
                result.id,
                reason=(
                    "RULE_BASED_EXTRACTION_REQUIRES_REVIEW"
                    if is_rule
                    else "PROVIDER_REQUIRES_REVIEW"
                    if decision.quality_level == "high"
                    else (
                        f"PARSE_QUALITY_{decision.quality_level.upper()}: "
                        f"score={decision.parse_quality_score:.2f}"
                    )
                ),
                priority=decision.review_priority or "normal",
            )
        return result

    def _extract_through_provider(
        self, uow: JDUoW, jd: JDDTO, provider: JDExtractionProvider
    ) -> tuple[JDSchemaPersistence, JDLegacyFields, object, dict[str, object]]:
        envelope = self._jd_envelope(jd)
        raw_bundle = provider.extract(envelope)
        bundle = parse_extracted_jd_bundle(raw_bundle.model_dump(mode="json"))
        validate_bundle_matches_envelope(envelope, bundle)
        cleaned_text = bundle.cleaned_text
        uow.jds.update_cleaned_text(jd.id, cleaned_text)
        catalog_skills, catalog_aliases = uow.catalog_entries()
        catalog_identity_metadata = self._catalog_identity_metadata(uow)
        material = map_bundle_to_framework_draft(
            bundle,
            framework_jd_id=jd.id,
            raw_text=cleaned_text,
            fallback_title=jd.title,
            catalog_skills=catalog_skills,
            catalog_aliases=catalog_aliases,
        )
        schema_bundle = require_port_result(
            self._schema.load(
                material.extraction_payload,
                material.normalization_payload,
            ),
            JDSchemaBundle,
            operation="JDSchemaPort.load",
        )
        stored = require_port_result(
            self._schema.persist(schema_bundle),
            JDSchemaPersistence,
            operation="JDSchemaPort.persist",
        )
        legacy = require_port_result(
            self._schema.legacy(schema_bundle, jd.title),
            JDLegacyFields,
            operation="JDSchemaPort.legacy",
        )
        return stored, legacy, bundle, catalog_identity_metadata

    @staticmethod
    def _catalog_identity_metadata(uow: JDUoW) -> dict[str, object]:
        skill = uow.catalog_identity()
        position = uow.position_catalog_identity()
        return {
            "catalog_identity": {
                "skill": {
                    "catalog_version": skill.catalog_version,
                    "content_hash": skill.content_hash,
                },
                "position": {
                    "catalog_version": position.catalog_version,
                    "content_hash": position.content_hash,
                },
            }
        }

    @staticmethod
    def _jd_envelope(jd: JDDTO) -> CrawlerJDEnvelopeV1:
        source_platform = (jd.source_type or "direct_text").strip().lower()
        source_record_id = (
            (jd.source_name or "").removeprefix("batch:").strip() or jd.id
        )
        raw_payload = {
            "source_platform": source_platform,
            "source_record_id": source_record_id,
            "title": jd.title,
        }
        return CrawlerJDEnvelopeV1(
            source_record_id=source_record_id,
            source_platform=source_platform,
            source_url=jd.url,
            job_title_raw=jd.title,
            crawl_time=datetime.now(timezone.utc),
            raw_text=jd.raw_text,
            raw_payload=raw_payload,
            text_canonicalization_version="extracted-jd-bundle-v1",
            source_version=str(jd.id),
        )

    def _versioned_parse_update(
        self,
        uow: JDUoW,
        jd: JDDTO,
        result: JDParseResultDTO,
        command: JDParseEditCommand,
    ) -> JDParseResultVersionedUpdate:
        extraction_payload = (
            command.extraction_result
            if "extraction_result" in command.changed_fields
            else result.extraction_result
        )
        normalization_payload = (
            command.normalized_result
            if "normalized_result" in command.changed_fields
            else None
        )
        if extraction_payload is None:
            raise _invalid("extraction_result is required")
        try:
            bundle = require_port_result(
                self._schema.edit(extraction_payload, normalization_payload),
                JDSchemaBundle,
                operation="JDSchemaPort.edit",
            )
            if bundle.document.document_id != jd.id:
                raise ValueError("extraction_result.document_id must match JD")
            bundle = self._resolve_schema_skills(uow, bundle)
            catalog_identity_metadata = self._catalog_identity_metadata(uow)
            stored = require_port_result(
                self._schema.persist(bundle),
                JDSchemaPersistence,
                operation="JDSchemaPort.persist",
            )
        except ValueError as exc:
            if "Only exact evidence" in str(exc):
                raise _conflict(str(exc)) from exc
            raise _invalid(str(exc)) from exc
        return JDParseResultVersionedUpdate(
            legacy=require_port_result(
                self._schema.legacy(bundle, jd.title),
                JDLegacyFields,
                operation="JDSchemaPort.legacy",
            ),
            extraction_result=stored.extraction_payload,
            normalized_result=stored.normalization_payload,
            schema_version=stored.schema_version,
            normalization_schema_version=stored.normalization_schema_version,
            need_review=True,
            workflow_status="draft",
            execution_metadata=catalog_identity_metadata,
        )

    @staticmethod
    def _resolve_schema_skills(
        uow: JDUoW, bundle: JDSchemaBundle
    ) -> JDSchemaBundle:
        """Bind legacy/manual normalization output to the authoritative Catalog."""

        if not any(
            item.item_type == "skill" for item in bundle.normalization.items
        ):
            return bundle
        skills, aliases = uow.catalog_entries()
        catalog_codes = {
            "skill_catalog_unresolved",
            "skill_catalog_conflict",
            "skill_catalog_snapshot_missing",
        }
        flags = [
            flag
            for flag in bundle.normalization.review_flags
            if flag.code not in catalog_codes
        ]
        items = []
        for item in bundle.normalization.items:
            if item.item_type != "skill":
                items.append(item)
                continue
            resolution = resolve_catalog_skill(
                source_name=item.source_value,
                claimed_skill_id=item.skill_id,
                claimed_canonical_name=item.canonical_name,
                skills=skills,
                aliases=aliases,
            )
            raw_payload = dict(item.raw_payload)
            raw_payload.setdefault("source_skill_id", item.skill_id)
            raw_payload.setdefault("source_canonical_name", item.canonical_name)
            raw_payload.setdefault("source_category_code", item.category_code)
            raw_payload.setdefault(
                "source_subcategory_code", item.subcategory_code
            )
            raw_payload.setdefault(
                "source_resolution_status", item.resolution_status
            )
            raw_payload["resolution_source"] = resolution.resolution_source
            items.append(
                replace(
                    item,
                    resolution_status=resolution.status,
                    skill_id=resolution.skill_id,
                    canonical_name=(
                        resolution.canonical_name or item.canonical_name
                    ),
                    category_code=resolution.category_code,
                    subcategory_code=(
                        None
                        if resolution.status == "resolved"
                        else item.subcategory_code
                    ),
                    raw_payload=raw_payload,
                )
            )
            if resolution.error_code is not None:
                flags.append(
                    ReviewFlag(
                        flag_type="skill",
                        source_value=item.source_value,
                        reason=resolution.error_code,
                        severity="blocking",
                        source="normalization",
                        code=resolution.error_code,
                        raw_payload={
                            "details": {
                                "source_skill_id": item.skill_id,
                                "catalog_resolution_status": resolution.status,
                            }
                        },
                    )
                )
        return JDSchemaBundle(
            document=bundle.document,
            normalization=replace(
                bundle.normalization,
                items=tuple(items),
                review_flags=tuple(flags),
            ),
        )

    def _assert_publishable_result(
        self, uow: JDUoW, result: JDParseResultDTO, *, for_confirmation: bool
    ) -> None:
        if result.extraction_result is None or result.normalized_result is None:
            raise _conflict(
                "Versioned extraction and normalization must exist before review"
                if for_confirmation
                else "Versioned extraction and normalization must exist before publication"
            )
        try:
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
            self._schema.validate_publishable(bundle)
        except (TypeError, ValueError) as exc:
            raise _conflict(str(exc)) from exc
        classification = dict(bundle.normalization.job_classification or {})
        if classification.get("schema_version") != "job-position-classification.v3":
            raise _conflict("position_classification_v3_required")
        if classification.get("taxonomy_version") != "position-taxonomy.v3.0.0":
            raise _conflict("position_taxonomy_version_incompatible")
        if classification.get("classification_status") not in {
            "resolved",
            "manually_confirmed",
        }:
            raise _conflict("position_classification_not_publishable")
        position_id = classification.get("position_id")
        position_code = classification.get("position_code")
        if not position_id or not position_code:
            raise _conflict("position_catalog_binding_missing")
        position_entry = uow.position_catalog_entry(str(position_id))
        if position_entry is None:
            raise _conflict("position_catalog_binding_missing")
        (
            _,
            bound_code,
            _,
            _,
            _,
            _,
            taxonomy_version,
            lifecycle_status,
        ) = position_entry
        if (
            bound_code != position_code
            or taxonomy_version != classification["taxonomy_version"]
            or lifecycle_status != "active"
        ):
            raise _conflict("position_catalog_binding_invalid")
        catalog_skills, _ = uow.catalog_entries()
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
                    skills=catalog_skills,
                )
        except SkillCatalogGateError as exc:
            raise _conflict(exc.code) from exc
        if any(
            flag.severity == "blocking" and flag.flag_type != "skill"
            for flag in bundle.normalization.review_flags
        ):
            raise _conflict(
                "Blocking review flags must be resolved before confirmation"
                if for_confirmation
                else "Blocking review flags must be resolved before publication"
            )

    def _duplicate_check(self, uow: JDUoW, jd: JDDTO) -> DuplicateCheckResult:
        others = require_port_list(
            uow.jds.all_other_jds(jd.id),
            JDDTO,
            operation="JDRepository.all_other_jds",
        )
        parse_results = {
            result.jd_id: result
            for result in require_port_list(
                uow.jds.list_parse_results([jd.id, *(other.id for other in others)]),
                JDParseResultDTO,
                operation="JDRepository.list_parse_results",
            )
        }
        required_skill_ids, bonus_skill_ids = _normalized_skill_ids(
            parse_results.get(jd.id)
        )
        candidates = []
        for other in others:
            other_required, other_bonus = _normalized_skill_ids(
                parse_results.get(other.id)
            )
            candidates.append(
                DuplicateCandidateFacts(
                    other.id,
                    other.cleaned_text or other.raw_text,
                    other.source_name,
                    other_required,
                    other_bonus,
                )
            )
        decision = evaluate_duplicate(DuplicateFacts(
            jd.id,
            jd.cleaned_text or jd.raw_text,
            tuple(candidates),
            required_skill_ids,
            bonus_skill_ids,
        ))
        require_port_result(
            uow.jds.update_jd(
                jd.id, JDCopyRiskUpdate(decision.copy_risk_score)
            ),
            JDDTO,
            operation="JDRepository.update_jd",
        )
        return DuplicateCheckResult(
            jd.id,
            decision.copy_risk_score,
            tuple(
                SimilarJD(
                    item.jd_id,
                    item.similarity,
                    item.source_name,
                    item.text_overlap,
                    item.skill_overlap,
                    item.length_similarity,
                )
                for item in decision.similar_jds
            ),
            decision.recommended_action,
            decision.reason,
        )

    def _inflation_check(
        self, uow: JDUoW, jd: JDDTO, result: JDParseResultDTO
    ) -> InflationCheckResult:
        decision = evaluate_inflation(inflation_facts_from_result(jd, result))
        require_port_result(
            uow.jds.update_jd(
                jd.id, JDInflationUpdate(decision.inflation_score)
            ),
            JDDTO,
            operation="JDRepository.update_jd",
        )
        return InflationCheckResult(
            jd.id,
            decision.inflation_score,
            tuple(
                AbnormalSkill(item.skill_id, item.skill_name, item.reason)
                for item in decision.abnormal_skills
            ),
            decision.recommended_action,
            decision.mismatch_reasons,
        )

    def _inflation_check_for_jd(
        self, uow: JDUoW, actor: Actor, jd: JDDTO
    ) -> InflationCheckResult:
        result = require_optional_port_result(
            uow.jds.get_parse_result(jd.id),
            JDParseResultDTO,
            operation="JDRepository.get_parse_result",
        )
        if result is None:
            raise _conflict("Parse the JD with an explicit extraction_mode first")
        return self._inflation_check(uow, jd, result)
