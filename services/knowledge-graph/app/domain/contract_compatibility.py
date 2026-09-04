"""Deterministic audit of Extraction-to-published-fact requirement grouping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRequirement:
    requirement_id: str
    kind: str
    modality: str
    skill_names: tuple[str, ...]
    evidence_source_id: str
    evidence_quote: str


@dataclass(frozen=True)
class NormalizedSkill:
    source_name: str
    resolution_status: str
    skill_id: str | None


@dataclass(frozen=True)
class NormalizedRequirement:
    requirement_id: str
    kind: str
    modality: str
    skills: tuple[NormalizedSkill, ...]


@dataclass(frozen=True)
class RequirementCompatibilityIssue:
    requirement_id: str
    reject_code: str
    detail: str


@dataclass(frozen=True)
class RequirementCompatibilityAudit:
    accepted: bool
    matched_requirement_ids: tuple[str, ...]
    unresolved_source_names: tuple[str, ...]
    issues: tuple[RequirementCompatibilityIssue, ...]


def audit_requirement_compatibility(
    source: tuple[SourceRequirement, ...],
    normalized: tuple[NormalizedRequirement, ...],
) -> RequirementCompatibilityAudit:
    source_ids = [item.requirement_id for item in source]
    normalized_ids = [item.requirement_id for item in normalized]
    issues: list[RequirementCompatibilityIssue] = []
    for requirement_id in sorted(set(source_ids)):
        if source_ids.count(requirement_id) > 1:
            issues.append(
                RequirementCompatibilityIssue(
                    requirement_id,
                    "DUPLICATE_SOURCE_REQUIREMENT_ID",
                    "source requirement_id must be unique",
                )
            )
    for requirement_id in sorted(set(normalized_ids)):
        if normalized_ids.count(requirement_id) > 1:
            issues.append(
                RequirementCompatibilityIssue(
                    requirement_id,
                    "DUPLICATE_NORMALIZED_REQUIREMENT_ID",
                    "normalized requirement_id must be unique",
                )
            )
    source_by_id = {item.requirement_id: item for item in source}
    normalized_by_id = {item.requirement_id: item for item in normalized}
    for requirement_id in sorted(set(source_by_id) - set(normalized_by_id)):
        issues.append(
            RequirementCompatibilityIssue(
                requirement_id,
                "NORMALIZED_REQUIREMENT_MISSING",
                "source requirement has no normalized counterpart",
            )
        )
    for requirement_id in sorted(set(normalized_by_id) - set(source_by_id)):
        issues.append(
            RequirementCompatibilityIssue(
                requirement_id,
                "SOURCE_REQUIREMENT_MISSING",
                "normalized requirement has no source counterpart",
            )
        )
    unresolved: list[str] = []
    matched: list[str] = []
    for requirement_id in sorted(set(source_by_id) & set(normalized_by_id)):
        source_item = source_by_id[requirement_id]
        normalized_item = normalized_by_id[requirement_id]
        if source_item.kind != normalized_item.kind:
            issues.append(
                RequirementCompatibilityIssue(
                    requirement_id,
                    "REQUIREMENT_KIND_MISMATCH",
                    f"source kind {source_item.kind} != normalized kind {normalized_item.kind}",
                )
            )
            continue
        if source_item.modality != normalized_item.modality:
            issues.append(
                RequirementCompatibilityIssue(
                    requirement_id,
                    "REQUIREMENT_MODALITY_MISMATCH",
                    "source and normalized modality differ",
                )
            )
        if source_item.kind != "skill" and normalized_item.skills:
            issues.append(
                RequirementCompatibilityIssue(
                    requirement_id,
                    "NON_SKILL_REQUIREMENT_HAS_SKILLS",
                    "education, experience, certificate, and soft-skill facts stay distinct",
                )
            )
            continue
        source_names = list(source_item.skill_names)
        normalized_names = [item.source_name for item in normalized_item.skills]
        if len(source_names) != len(set(source_names)):
            issues.append(
                RequirementCompatibilityIssue(
                    requirement_id,
                    "AMBIGUOUS_SOURCE_SKILL_NAME",
                    "a skill source_name occurs more than once inside one requirement",
                )
            )
        if sorted(source_names) != sorted(normalized_names):
            issues.append(
                RequirementCompatibilityIssue(
                    requirement_id,
                    "NORMALIZED_SKILL_SET_MISMATCH",
                    "normalized skills must preserve the source requirement skill set",
                )
            )
        for skill in normalized_item.skills:
            if skill.resolution_status == "resolved" and not skill.skill_id:
                issues.append(
                    RequirementCompatibilityIssue(
                        requirement_id,
                        "RESOLVED_SKILL_ID_REQUIRED",
                        f"resolved skill {skill.source_name} has no standard skill ID",
                    )
                )
            elif skill.resolution_status == "unresolved":
                if skill.skill_id is not None:
                    issues.append(
                        RequirementCompatibilityIssue(
                            requirement_id,
                            "UNRESOLVED_SKILL_ID_FORBIDDEN",
                            f"unresolved skill {skill.source_name} cannot carry a standard skill ID",
                        )
                    )
                unresolved.append(skill.source_name)
            elif skill.resolution_status != "resolved":
                issues.append(
                    RequirementCompatibilityIssue(
                        requirement_id,
                        "INVALID_SKILL_RESOLUTION_STATUS",
                        f"unsupported resolution status {skill.resolution_status}",
                    )
                )
        matched.append(requirement_id)
    return RequirementCompatibilityAudit(
        accepted=not issues,
        matched_requirement_ids=tuple(matched),
        unresolved_source_names=tuple(sorted(unresolved)),
        issues=tuple(issues),
    )
