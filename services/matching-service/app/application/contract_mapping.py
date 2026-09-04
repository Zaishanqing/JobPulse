"""Explicit mapping from strict upstream bundles into matching Domain profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Generic, TypeVar

from pydantic import ValidationError

from app.domain.integration import ContractIssue, SourceVersionRecord
from app.domain.privacy import find_pii
from app.domain.profiles import (
    CapabilityEvidenceLink,
    CapabilityProfile,
    CredentialFeature,
    CVMatchProfile,
    CVSkill,
    EducationFeature,
    Evidence,
    ExperienceFeature,
    HardCondition,
    LanguageFeature,
    MatchFeature,
    PositionContext,
    PositionClassificationRef,
    ResearchOutputFeature,
    PositionMatchProfile,
    PositionResponsibilityRequirement,
    PositionSkillRequirement,
    QualityContext,
    TrendContext,
    UnresolvedItem,
)
from app.ports.upstream_contracts import (
    ExternalCVBundle,
    ExternalEvidence,
    ExternalPositionBundle,
)

T = TypeVar("T")
_FULL_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTH_DATE_RE = re.compile(r"^(\d{4})[.-](\d{2})$")
_YEAR_DATE_RE = re.compile(r"^(\d{4})$")
_YEAR_LEVEL_RE = re.compile(r"^(\d{4})级$")
_OPEN_ENDED_MARKERS = frozenset({"至今", "present", "current", ""})
_LEVEL_MAP = {
    "know": "basic",
    "familiar": "working",
    "proficient": "proficient",
    "expert": "expert",
}


def _start_of_month(year: int, month: int) -> date:
    return date(year, month, 1)


def _next_month(year: int, month: int) -> date:
    month += 1
    if month == 13:
        year += 1
        month = 1
    return date(year, month, 1)


def _parse_experience_date(
    value: str | None, as_of_date: date, *, end: bool = False
) -> date | None:
    if value is None:
        return as_of_date if end else None
    normalized = value.strip()
    if normalized in _OPEN_ENDED_MARKERS:
        return as_of_date if end else None
    full = _FULL_DATE_RE.fullmatch(normalized)
    if full:
        try:
            return date(
                int(full.group(1)), int(full.group(2)), int(full.group(3))
            )
        except ValueError:
            return None
    month = _MONTH_DATE_RE.fullmatch(normalized)
    if month:
        try:
            year = int(month.group(1))
            month_number = int(month.group(2))
            if month_number < 1 or month_number > 12:
                return None
            if end:
                return _next_month(year, month_number)
            return _start_of_month(year, month_number)
        except ValueError:
            return None
    year = _YEAR_DATE_RE.fullmatch(normalized) or _YEAR_LEVEL_RE.fullmatch(normalized)
    if year:
        parsed_year = int(year.group(1))
        return date(parsed_year + 1, 1, 1) if end else date(parsed_year, 1, 1)
    return None


@dataclass(frozen=True)
class MappingOutcome(Generic[T]):
    value: T | None
    issues: tuple[ContractIssue, ...]
    versions: tuple[SourceVersionRecord, ...]


def _evidence(item: ExternalEvidence) -> Evidence:
    return Evidence.model_validate(item.model_dump(mode="python"))


def _evidence_many(items: tuple[ExternalEvidence, ...]) -> tuple[Evidence, ...]:
    return tuple(_evidence(item) for item in items)


def _dedupe_evidence(*groups: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    unique = {
        (item.source_id, item.start, item.end, item.quote): item
        for group in groups
        for item in group
    }
    return tuple(unique[key] for key in sorted(unique, key=str))


def _validation_issues(side: str, exc: ValidationError) -> tuple[ContractIssue, ...]:
    return tuple(
        ContractIssue(
            side=side,
            code="UPSTREAM_SCHEMA_INVALID",
            path="$"
            + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in item["loc"]),
            message=item["msg"],
            severity="error",
        )
        for item in exc.errors(include_url=False)
    )


def _privacy_issues(side: str, payload: object) -> tuple[ContractIssue, ...]:
    return tuple(
        ContractIssue(
            side=side,
            code="PII_FORBIDDEN",
            path=item.path,
            message=item.reason,
            severity="error",
        )
        for item in find_pii(payload)
    )


def _unresolved(item) -> UnresolvedItem:
    return UnresolvedItem(
        item_id=item.item_id,
        item_type=item.item_type,
        raw_value=item.raw_value,
        reason=item.reason,
        evidence_refs=_evidence_many(item.evidence),
    )


def map_cv_bundle(payload: object) -> MappingOutcome[CVMatchProfile]:
    privacy = _privacy_issues("cv", payload)
    if privacy:
        return MappingOutcome(None, privacy, ())
    try:
        bundle = ExternalCVBundle.model_validate(payload)
    except ValidationError as exc:
        return MappingOutcome(None, _validation_issues("cv", exc), ())
    versions = (
        SourceVersionRecord(
            source_system=bundle.source_system,
            contract_version=bundle.contract_version,
            taxonomy_version=bundle.normalization.taxonomy_version,
            derivation_version=bundle.structure_derivation_version,
            snapshot_id=bundle.verification_snapshot_id,
            source_version=bundle.source_version,
            created_at=bundle.created_at,
        ),
        SourceVersionRecord(
            source_system="cv-extraction-match-features",
            contract_version="CVMatchFeatureResult",
            taxonomy_version=bundle.match_features.taxonomy_version,
            derivation_version=bundle.match_features.derivation_version,
        ),
        SourceVersionRecord(
            source_system="cv-extraction-capability-verification",
            contract_version="CVCapabilityVerificationResult",
            taxonomy_version=bundle.capabilities.taxonomy_version,
            derivation_version=bundle.capabilities.derivation_version,
        ),
    )
    issues: list[ContractIssue] = []
    document_ids = {
        bundle.structure.document_id,
        bundle.normalization.document_id,
        bundle.match_features.document_id,
        bundle.capabilities.document_id,
    }
    if len(document_ids) != 1:
        issues.append(
            ContractIssue(
                side="cv",
                code="CV_DOCUMENT_ID_MISMATCH",
                path="$.document_id",
                message="CV extraction bundle document IDs must be identical",
                severity="error",
            )
        )
    taxonomy_versions = {
        bundle.normalization.taxonomy_version,
        bundle.match_features.taxonomy_version,
        bundle.capabilities.taxonomy_version,
        *(item.taxonomy_version for item in bundle.match_features.features),
        *(item.taxonomy_version for item in bundle.capabilities.evidence_links),
    }
    if len(taxonomy_versions) != 1:
        issues.append(
            ContractIssue(
                side="cv",
                code="CV_TAXONOMY_VERSION_MISMATCH",
                path="$.taxonomy_version",
                message="normalization, MatchFeature and capability taxonomy versions differ",
                severity="error",
            )
        )
    for index, item in enumerate(bundle.normalization.skills):
        if item.resolution_status == "resolved" and item.normalization_confidence is None:
            issues.append(
                ContractIssue(
                    side="cv",
                    code="CV_NORMALIZATION_CONFIDENCE_MISSING",
                    path=f"$.normalization.skills[{index}].normalization_confidence",
                    message="resolved normalized skills require normalization confidence",
                    severity="error",
                    source_version=bundle.normalization.derivation_version,
                )
            )
        if item.resolution_status == "resolved" and item.resolution_source in {None, "unresolved"}:
            issues.append(
                ContractIssue(
                    side="cv",
                    code="CV_NORMALIZATION_RESOLUTION_SOURCE_MISSING",
                    path=f"$.normalization.skills[{index}].resolution_source",
                    message="resolved normalized skills require a resolution source",
                    severity="error",
                    source_version=bundle.normalization.derivation_version,
                )
            )
        if item.resolution_status == "resolved" and not (item.skill_id and item.canonical_name):
            issues.append(
                ContractIssue(
                    side="cv",
                    code="CV_NORMALIZED_SKILL_IDENTITY_MISSING",
                    path=f"$.normalization.skills[{index}]",
                    message="resolved skill requires skill_id and canonical_name",
                    severity="error",
                )
            )
    as_of_date = bundle.match_features.as_of_date
    date_unresolved: list[UnresolvedItem] = []
    for experience in bundle.structure.work_experiences + bundle.structure.projects:
        for field_name, value in (
            ("start_date", experience.start_date),
            ("end_date", experience.end_date),
        ):
            if value is not None and _parse_experience_date(
                value, as_of_date, end=(field_name == "end_date")
            ) is None:
                issues.append(
                    ContractIssue(
                        side="cv",
                        code="CV_DATE_PRECISION_UNSUPPORTED",
                        path=f"$.structure.{experience.experience_id}.{field_name}",
                        message=(
                            "experience date must be YYYY-MM-DD, YYYY-MM, "
                            "YYYY, YYYY级, or an open-ended end marker; "
                            "value remains unresolved"
                        ),
                        severity="warning",
                        source_version=bundle.structure_derivation_version,
                    )
                )
                date_unresolved.append(
                    UnresolvedItem(
                        item_id=f"{experience.experience_id}:{field_name}",
                        item_type="experience_date",
                        raw_value=value,
                        reason="date is not a supported precision or open-ended value",
                        evidence_refs=_evidence_many(experience.evidence),
                    )
                )
    if any(item.severity == "error" for item in issues):
        return MappingOutcome(None, tuple(issues), versions)
    capabilities_by_skill = {
        item.skill_id: item for item in bundle.capabilities.profiles if item.skill_id
    }
    skills = []
    for item in bundle.normalization.skills:
        capability = capabilities_by_skill.get(item.skill_id)
        skills.append(
            CVSkill(
                aggregation_key=(
                    capability.aggregation_key
                    if capability
                    else f"skill:{item.skill_id or item.source_item_id}"
                ),
                skill_id=item.skill_id,
                canonical_name=item.canonical_name,
                normalization_confidence=item.normalization_confidence or 0.0,
                resolution_source=item.resolution_source or "legacy_unspecified",
                declared_level=item.declared_level,
                demonstrated_level=(capability.demonstrated_level if capability else "unknown"),
                verification_status=(
                    capability.verification_status if capability else "not_observed"
                ),
                resolution_status=item.resolution_status,
                evidence_refs=_evidence_many(item.evidence),
            )
        )
    normalized_by_source = {item.source_item_id: item for item in bundle.normalization.skills}

    def experience(item) -> ExperienceFeature:
        tool_ids = tuple(
            sorted(
                {
                    normalized_by_source[source_id].skill_id
                    for source_id in item.tool_source_item_ids
                    if source_id in normalized_by_source
                    and normalized_by_source[source_id].resolution_status == "resolved"
                    and normalized_by_source[source_id].skill_id is not None
                }
            )
        )
        evidence = _dedupe_evidence(
            _evidence_many(item.evidence),
            tuple(_evidence(value.evidence) for value in item.responsibilities),
            tuple(_evidence(value.evidence) for value in item.business_scenarios),
        )
        return ExperienceFeature(
            experience_id=item.experience_id,
            kind=item.kind,
            role=item.role,
            responsibilities=tuple(value.value for value in item.responsibilities),
            business_scenarios=tuple(value.value for value in item.business_scenarios),
            tool_skill_ids=tool_ids,
            start_date=_parse_experience_date(item.start_date, as_of_date),
            end_date=_parse_experience_date(item.end_date, as_of_date, end=True),
            evidence_refs=evidence,
        )

    match_features = tuple(
        MatchFeature.model_validate(item.model_dump(mode="python"))
        for item in bundle.match_features.features
    )
    # Ensure the formal CV profile always exposes sentence-level task
    # evidence, even when an older extractor only populated structured
    # responsibilities. New extractors should emit these features upstream;
    # this boundary fallback keeps old bundles relation-groundable.
    bound_experience_ids = {
        item.source_object_id
        for item in match_features
        if item.feature_type in {"task", "experience"}
    }
    generated_task_features = tuple(
        MatchFeature(
            feature_id=f"{experience.experience_id}:task:{index}",
            document_id=bundle.structure.document_id,
            side="cv",
            feature_type="task",
            source_object_id=experience.experience_id,
            source_scope=f"{experience.kind}_experience:{experience.experience_id}:responsibility",
            raw_text=value.value,
            resolution_status="resolved",
            evidence_refs=(_evidence(value.evidence),),
            taxonomy_version=bundle.match_features.taxonomy_version,
            derivation_version=bundle.match_features.derivation_version,
        )
        for experience in bundle.structure.work_experiences + bundle.structure.projects
        if experience.experience_id not in bound_experience_ids
        for index, value in enumerate(experience.responsibilities, 1)
    )
    match_features = match_features + generated_task_features
    capability_profiles = tuple(
        CapabilityProfile.model_validate(item.model_dump(mode="python"))
        for item in bundle.capabilities.profiles
    )
    capability_links = tuple(
        CapabilityEvidenceLink.model_validate(item.model_dump(mode="python"))
        for item in bundle.capabilities.evidence_links
    )
    all_evidence = _dedupe_evidence(
        _evidence_many(bundle.structure.evidence),
        tuple(value for item in skills for value in item.evidence_refs),
        tuple(value for item in match_features for value in item.evidence_refs),
        tuple(value for item in capability_links for value in item.evidence_refs),
        tuple(
            value
            for item in bundle.structure.research_outputs
            for value in _evidence_many(item.evidence)
        ),
    )
    unresolved = tuple(_unresolved(item) for item in bundle.unresolved_items) + tuple(
        date_unresolved
    )
    profile = CVMatchProfile(
        schema_version="cv-match-profile.v1",
        contract_version="cv-match-profile.v1",
        source_version=bundle.source_version,
        created_at=bundle.created_at,
        cv_id=bundle.structure.document_id,
        user_id=bundle.user_ref,
        verification_snapshot_id=bundle.verification_snapshot_id,
        as_of_date=bundle.match_features.as_of_date,
        skills=tuple(skills),
        match_features=match_features,
        capability_profiles=capability_profiles,
        capability_evidence_links=capability_links,
        projects=tuple(experience(item) for item in bundle.structure.projects),
        work_experiences=tuple(experience(item) for item in bundle.structure.work_experiences),
        education=tuple(
            EducationFeature(
                education_id=item.education_id,
                degree_level=item.degree_level,
                field_of_study=item.field_of_study,
                resolution_status=item.resolution_status,
                evidence_refs=_evidence_many(item.evidence),
            )
            for item in bundle.structure.education
        ),
        certificates=tuple(
            CredentialFeature(
                credential_id=item.credential_id,
                name=item.name,
                level=item.level,
                resolution_status=item.resolution_status,
                evidence_refs=_evidence_many(item.evidence),
            )
            for item in bundle.structure.certificates
        ),
        languages=tuple(
            LanguageFeature(
                language_code=item.language_code,
                proficiency=item.proficiency,
                resolution_status=item.resolution_status,
                evidence_refs=_evidence_many(item.evidence),
            )
            for item in bundle.structure.languages
        ),
        evidence_refs=all_evidence,
        unresolved_items=unresolved,
        review_status=bundle.review_status,
        taxonomy_version=bundle.normalization.taxonomy_version,
        derivation_version=(
            "upstream-contract-map.v1|"
            f"structure={bundle.structure_derivation_version}|"
            f"normalization={bundle.normalization.derivation_version}|"
            f"features={bundle.match_features.derivation_version}|"
            f"capabilities={bundle.capabilities.derivation_version}"
        ),
        position_classifications=tuple(
            PositionClassificationRef(
                taxonomy_version=feature.taxonomy_version,
                position_code=(
                    str(feature.structured_values["position_code"])
                    if feature.structured_values.get("position_code")
                    else None
                ),
                classification_status=str(
                    feature.structured_values.get(
                        "classification_status", "ambiguous"
                    )
                ),
                career_level=(
                    str(feature.structured_values["career_level"])
                    if feature.structured_values.get("career_level")
                    else None
                ),
                leadership_scope=(
                    str(feature.structured_values["leadership_scope"])
                    if feature.structured_values.get("leadership_scope")
                    else None
                ),
            )
            for feature in match_features
            if feature.feature_type == "role"
        ),
        research_outputs=tuple(
            ResearchOutputFeature(
                output_id=item.output_id,
                output_type=item.output_type,
                title=item.title,
                status=item.status,
                role=item.role,
                order=item.order,
                year=item.year,
                date=item.date,
                url=item.url,
                evidence_refs=_evidence_many(item.evidence),
            )
            for item in bundle.structure.research_outputs
        ),
    )
    return MappingOutcome(
        profile,
        tuple(issues),
        versions,
    )


def map_authoritative_cv_profile(payload: object) -> MappingOutcome[CVMatchProfile]:
    if isinstance(payload, dict) and (
        payload.get("schema_version") == "cv-match-profile.v1"
        or payload.get("contract_version") == "cv-match-profile.v1"
    ):
        try:
            return MappingOutcome(CVMatchProfile.model_validate(payload), (), ())
        except ValidationError as exc:
            return MappingOutcome(None, _validation_issues("cv", exc), ())
    return map_cv_bundle(payload)


def map_position_bundle(payload: object) -> MappingOutcome[PositionMatchProfile]:
    privacy = _privacy_issues("position", payload)
    if privacy:
        return MappingOutcome(None, privacy, ())
    try:
        bundle = ExternalPositionBundle.model_validate(payload)
    except ValidationError as exc:
        return MappingOutcome(None, _validation_issues("position", exc), ())
    versions = (
        SourceVersionRecord(
            source_system=bundle.source_system,
            contract_version=bundle.contract_version,
            taxonomy_version=bundle.jd_normalization.taxonomy_version,
            derivation_version=bundle.jd_normalization.derivation_version,
            snapshot_id=bundle.standard_position.snapshot_id,
            graph_version=bundle.graph.graph_version,
            source_version=bundle.source_version,
            created_at=bundle.created_at,
        ),
        SourceVersionRecord(
            source_system=bundle.graph.source_system,
            contract_version="published-position-graph",
            snapshot_id=bundle.graph.snapshot_id,
            graph_version=bundle.graph.graph_version,
        ),
    )
    issues: list[ContractIssue] = []
    if bundle.jd_extraction.document_id != bundle.jd_normalization.document_id:
        issues.append(
            ContractIssue(
                side="position",
                code="JD_DOCUMENT_ID_MISMATCH",
                path="$.jd_extraction.document_id",
                message="JD extraction and normalization document IDs differ",
                severity="error",
            )
        )
    if bundle.standard_position.position_id != bundle.graph.position_id:
        issues.append(
            ContractIssue(
                side="position",
                code="POSITION_GRAPH_ID_MISMATCH",
                path="$.graph.position_id",
                message="standard position and graph position IDs differ",
                severity="error",
            )
        )
    if bundle.standard_position.taxonomy_version != bundle.jd_normalization.taxonomy_version:
        issues.append(
            ContractIssue(
                side="position",
                code="POSITION_TAXONOMY_VERSION_MISMATCH",
                path="$.standard_position.taxonomy_version",
                message="JD and standard position taxonomy versions differ",
                severity="error",
            )
        )
    if bundle.jd_normalization.classification_status in {
        "resolved",
        "manually_confirmed",
    } and (
        not bundle.jd_normalization.position_code
        or bundle.jd_normalization.position_code
        != bundle.standard_position.position_code
    ):
        issues.append(
            ContractIssue(
                side="position",
                code="POSITION_CLASSIFICATION_ID_MISMATCH",
                path="$.jd_normalization.position_code",
                message="resolved JD classification must match standard position",
                severity="error",
            )
        )
    extraction_requirements = {
        item.requirement_id: item for item in bundle.jd_extraction.requirements
    }
    position_skills = {
        item.skill_id: item
        for item in (
            bundle.standard_position.required_skills + bundle.standard_position.bonus_skills
        )
    }
    required_skills = []
    preferred_skills = []
    for req_index, requirement in enumerate(bundle.jd_normalization.requirements):
        extraction = extraction_requirements.get(requirement.requirement_id)
        if extraction is None:
            issues.append(
                ContractIssue(
                    side="position",
                    code="JD_REQUIREMENT_LINK_MISSING",
                    path=f"$.jd_normalization.requirements[{req_index}]",
                    message="normalized requirement has no extraction requirement",
                    severity="error",
                )
            )
            continue
        for skill_index, skill in enumerate(requirement.skills):
            standard = position_skills.get(skill.skill_id or "")
            if skill.resolution_status == "resolved" and standard is None:
                issues.append(
                    ContractIssue(
                        side="position",
                        code="STANDARD_POSITION_SKILL_MISSING",
                        path=(
                            f"$.jd_normalization.requirements[{req_index}].skills[{skill_index}]"
                        ),
                        message="resolved JD skill is absent from standard position snapshot",
                        severity="error",
                    )
                )
                continue
            level = (
                _LEVEL_MAP.get(extraction.proficiency)
                if extraction.proficiency is not None
                else None
            )
            target = required_skills if requirement.modality == "required" else preferred_skills
            target.append(
                PositionSkillRequirement(
                    skill_id=skill.skill_id,
                    canonical_name=skill.canonical_name,
                    required_level=level,
                    importance=standard.weight if standard else 0.0,
                    resolution_status=skill.resolution_status,
                    evidence_refs=(_evidence(extraction.evidence),),
                )
            )
    if any(item.severity == "error" for item in issues):
        return MappingOutcome(None, tuple(issues), versions)
    if bundle.jd_normalization.classification_status not in {
        "resolved",
        "manually_confirmed",
    }:
        issues.append(
            ContractIssue(
                side="position",
                code="POSITION_CLASSIFICATION_NOT_RESOLVED",
                path="$.jd_normalization.classification_status",
                message="position profile requires a resolved classification",
                severity="error",
            )
        )
    if bundle.standard_position.sample_support_status != "sufficient":
        issues.append(
            ContractIssue(
                side="position",
                code="POSITION_SAMPLE_SUPPORT_INSUFFICIENT",
                path="$.standard_position.sample_support_status",
                message="formal position profiles require sufficient sample support",
                severity="error",
            )
        )
    if any(item.severity == "error" for item in issues):
        return MappingOutcome(None, tuple(issues), versions)
    hard_conditions = []
    for item in bundle.jd_extraction.requirements:
        if item.kind == "education" and item.minimum_degree:
            hard_conditions.append(
                HardCondition(
                    condition_id=item.requirement_id,
                    condition_type="education",
                    operator="at_least",
                    value=item.minimum_degree,
                    resolution_status="resolved",
                    evidence_refs=(_evidence(item.evidence),),
                )
            )
        elif item.kind == "experience" and item.minimum_years is not None:
            hard_conditions.append(
                HardCondition(
                    condition_id=item.requirement_id,
                    condition_type="experience",
                    operator="at_least",
                    value=f"{item.minimum_years:g} years",
                    resolution_status="resolved",
                    evidence_refs=(_evidence(item.evidence),),
                )
            )
        elif item.kind == "certificate" and item.certificates:
            hard_conditions.append(
                HardCondition(
                    condition_id=item.requirement_id,
                    condition_type="certificate",
                    operator="one_of",
                    value="|".join(item.certificates),
                    resolution_status="resolved",
                    evidence_refs=(_evidence(item.evidence),),
                )
            )
    if bundle.jd_extraction.locations:
        hard_conditions.append(
            HardCondition(
                condition_id="jd:employment:location",
                condition_type="location",
                operator="one_of",
                value="|".join(item.value for item in bundle.jd_extraction.locations),
                resolution_status="resolved",
                evidence_refs=tuple(
                    _evidence(item.evidence) for item in bundle.jd_extraction.locations
                ),
            )
        )
    responsibilities = tuple(item.text.value for item in bundle.jd_extraction.responsibilities)
    responsibility_evidence = tuple(
        _evidence(item.text.evidence) for item in bundle.jd_extraction.responsibilities
    )
    industry_evidence = tuple(_evidence(item.evidence) for item in bundle.jd_extraction.industries)
    unresolved = tuple(_unresolved(item) for item in bundle.jd_normalization.unresolved_items)
    all_evidence = _dedupe_evidence(
        (_evidence(bundle.jd_extraction.title.evidence),),
        responsibility_evidence,
        industry_evidence,
        tuple(value for item in required_skills for value in item.evidence_refs),
        tuple(value for item in preferred_skills for value in item.evidence_refs),
        tuple(value for item in hard_conditions for value in item.evidence_refs),
    )
    profile = PositionMatchProfile(
        schema_version="position-match-profile.v1",
        contract_version="position-match-profile.v1",
        source_version=bundle.source_version,
        created_at=bundle.created_at,
        position_id=bundle.jd_extraction.document_id,
        canonical_position_id=bundle.standard_position.position_id,
        canonical_title=bundle.standard_position.position_name,
        core_responsibilities=responsibilities,
        responsibility_requirements=tuple(
            PositionResponsibilityRequirement(
                requirement_id=item.requirement_id,
                text=item.text.value,
                evidence_refs=(_evidence(item.text.evidence),),
            )
            for item in bundle.jd_extraction.responsibilities
        ),
        required_skills=tuple(required_skills),
        preferred_skills=tuple(preferred_skills),
        hard_conditions=tuple(hard_conditions),
        tools=PositionContext(
            values=tuple(
                item.skill_name
                for item in position_skills.values()
                if item.skill_id.startswith("TOOL_")
            )
        ),
        industries=PositionContext(
            values=tuple(item.value for item in bundle.jd_extraction.industries),
            evidence_refs=industry_evidence,
        ),
        business_scenarios=PositionContext(values=bundle.standard_position.business_scenarios),
        evidence_refs=all_evidence,
        quality_context=QualityContext(
            snapshot_id=bundle.quality.snapshot_id,
            status=bundle.quality.status,
            completeness=bundle.quality.completeness,
            assessed_at=bundle.quality.assessed_at,
            evidence_refs=_evidence_many(bundle.quality.evidence),
        ),
        trend_context=(
            TrendContext(
                snapshot_id=bundle.trend.snapshot_id,
                window_start=bundle.trend.window_start,
                window_end=bundle.trend.window_end,
                trend_version=bundle.trend.trend_version,
                signals=bundle.trend.signals,
                evidence_refs=_evidence_many(bundle.trend.evidence),
            )
            if bundle.trend
            else None
        ),
        unresolved_items=unresolved,
        review_status=bundle.standard_position.review_status,
        taxonomy_version=bundle.standard_position.taxonomy_version,
        graph_version=bundle.graph.graph_version,
        position_code=bundle.standard_position.position_code,
        classification_status=bundle.jd_normalization.classification_status,
        career_level=bundle.jd_normalization.career_level,
        leadership_scope=bundle.jd_normalization.leadership_scope,
        sample_support_status=bundle.standard_position.sample_support_status,
    )
    return MappingOutcome(
        profile,
        tuple(issues),
        versions,
    )


def map_authoritative_position_profile(
    payload: object,
) -> MappingOutcome[PositionMatchProfile]:
    if (
        isinstance(payload, dict)
        and (
            payload.get("schema_version") == "position-match-profile.v1"
            or payload.get("contract_version") == "position-match-profile.v1"
        )
    ):
        try:
            return MappingOutcome(PositionMatchProfile.model_validate(payload), (), ())
        except ValidationError as exc:
            return MappingOutcome(None, _validation_issues("position", exc), ())
    return map_position_bundle(payload)
