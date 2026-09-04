from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Iterable

from .models import (
    CVExtractionResult,
    CVMatchFeatureResult,
    CVNormalizedResult,
    Evidence,
    MatchFeature,
    MatchFeatureType,
    MatchScalar,
    SkillItem,
)
from .salary_parser import parse_salary


MATCH_FEATURE_DERIVATION_VERSION = "1.3"

DEGREE_RANK = {
    "unknown": 0,
    "high_school": 1,
    "associate": 2,
    "bachelor": 3,
    "master": 4,
    "doctor": 5,
    "postdoc": 6,
}

LANGUAGE_ALIASES = {
    "中文": ("LANGUAGE_CHINESE", "中文"),
    "汉语": ("LANGUAGE_CHINESE", "中文"),
    "普通话": ("LANGUAGE_CHINESE", "中文"),
    "英语": ("LANGUAGE_ENGLISH", "英语"),
    "英文": ("LANGUAGE_ENGLISH", "英语"),
    "english": ("LANGUAGE_ENGLISH", "英语"),
    "日语": ("LANGUAGE_JAPANESE", "日语"),
    "日本语": ("LANGUAGE_JAPANESE", "日语"),
    "japanese": ("LANGUAGE_JAPANESE", "日语"),
    "韩语": ("LANGUAGE_KOREAN", "韩语"),
    "朝鲜语": ("LANGUAGE_KOREAN", "韩语"),
    "korean": ("LANGUAGE_KOREAN", "韩语"),
    "法语": ("LANGUAGE_FRENCH", "法语"),
    "french": ("LANGUAGE_FRENCH", "法语"),
    "德语": ("LANGUAGE_GERMAN", "德语"),
    "german": ("LANGUAGE_GERMAN", "德语"),
    "西班牙语": ("LANGUAGE_SPANISH", "西班牙语"),
    "spanish": ("LANGUAGE_SPANISH", "西班牙语"),
}

_PRESENT_TOKENS = {"至今", "现在", "目前", "今", "present", "current", "now"}
_DATE_PATTERN = re.compile(r"(?P<year>19\d{2}|20\d{2})(?:\s*[./\-年]\s*(?P<month>1[0-2]|0?[1-9]))?")


def _normalization_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def classify_role(title: str, normalization_map: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_status": "unresolved",
        "resolution_source": "position-taxonomy-v3-review-required",
        "source_title": title,
        "taxonomy_version": normalization_map["position_taxonomy_version"],
    }


def _month_index(value: str | None, *, as_of_date: date) -> int | None:
    if value is None:
        return None
    normalized = _normalization_key(value).strip(" .-/年月")
    if normalized in _PRESENT_TOKENS:
        return as_of_date.year * 12 + as_of_date.month - 1
    match = _DATE_PATTERN.search(unicodedata.normalize("NFKC", value))
    if match is None:
        return None
    year = int(match.group("year"))
    month = int(match.group("month") or 1)
    return year * 12 + month - 1


def _duration_values(start: str | None, end: str | None, *, as_of_date: date) -> dict[str, MatchScalar]:
    start_index = _month_index(start, as_of_date=as_of_date)
    end_index = _month_index(end, as_of_date=as_of_date)
    values: dict[str, MatchScalar] = {}
    if start is not None:
        values["date_start"] = start
    if end is not None:
        values["date_end"] = end
    if start_index is not None and end_index is not None and end_index >= start_index:
        values["duration_months"] = end_index - start_index + 1
    return values


def _merge_month_intervals(intervals: Iterable[tuple[int, int]]) -> int:
    ordered = sorted(intervals)
    if not ordered:
        return 0
    merged: list[list[int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start + 1 for start, end in merged)


def _feature_id(
    document_id: str,
    feature_type: MatchFeatureType,
    source_scope: str,
    source_object_id: str,
) -> str:
    return f"mf_cv_{document_id}_{feature_type}_{source_scope}_{source_object_id}"


def _feature(
    *,
    extraction: CVExtractionResult,
    feature_type: MatchFeatureType,
    source_object_id: str,
    source_scope: str,
    raw_text: str,
    taxonomy_version: str,
    evidence_refs: list[Evidence],
    canonical_id: str | None = None,
    canonical_name: str | None = None,
    vector_text: str | None = None,
    candidate_level: str | None = None,
    structured_values: dict[str, MatchScalar] | None = None,
    resolution_status: str = "resolved",
) -> MatchFeature:
    return MatchFeature(
        feature_id=_feature_id(
            extraction.document_id, feature_type, source_scope, source_object_id
        ),
        document_id=extraction.document_id,
        side="cv",
        feature_type=feature_type,
        source_object_id=source_object_id,
        source_scope=source_scope,
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        raw_text=raw_text,
        vector_text=vector_text,
        candidate_level=candidate_level,
        structured_values=structured_values or {},
        resolution_status=resolution_status,
        evidence_refs=evidence_refs,
        taxonomy_version=taxonomy_version,
        derivation_version=MATCH_FEATURE_DERIVATION_VERSION,
    )


def _field_evidence(entry: Any, field_name: str) -> Evidence:
    matches = [
        binding.evidence
        for binding in entry.field_evidence
        if binding.field_name == field_name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one field evidence binding for {field_name!r}, got {len(matches)}"
        )
    return matches[0]


def _field_evidences(entry: Any, field_names: Iterable[str]) -> list[Evidence]:
    return [_field_evidence(entry, field_name) for field_name in field_names]


def _skill_aggregation_key(item: SkillItem, canonical_id: str | None) -> str:
    if canonical_id is not None:
        return f"canonical:{canonical_id}"
    return f"raw:{item.item_type}:{_normalization_key(item.name)}"


def _explicit_candidate_level(value: str | None) -> str | None:
    return None if value in (None, "unknown") else value


def _skill_features(
    extraction: CVExtractionResult,
    normalized: CVNormalizedResult,
    taxonomy_version: str,
) -> list[MatchFeature]:
    normalized_by_item = {item.source_item_id: item for item in normalized.normalized_skills}
    scoped_items: list[tuple[str, SkillItem]] = [("skills", item) for item in extraction.skills]
    scoped_items.extend(
        (f"work_experience:{work.entry_id}:tech_stack", item)
        for work in extraction.work_experience
        for item in work.tech_stack
    )
    scoped_items.extend(
        (f"project_experience:{project.entry_id}:tech_stack", item)
        for project in extraction.project_experience
        for item in project.tech_stack
    )
    features: list[MatchFeature] = []
    for source_scope, item in scoped_items:
        occurrence_kind = (
            "declared" if source_scope == "skills"
            else "work" if source_scope.startswith("work_experience:")
            else "project"
        )
        if item.item_type == "soft_skill":
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="soft_skill",
                    source_object_id=item.item_id,
                    source_scope=source_scope,
                    raw_text=item.name,
                    vector_text=f"软技能：{item.name}",
                    candidate_level=_explicit_candidate_level(item.proficiency),
                    structured_values={
                        "aggregation_key": _skill_aggregation_key(item, None),
                        "occurrence_kind": occurrence_kind,
                        "proficiency_explicit": item.proficiency not in (None, "unknown"),
                    },
                    evidence_refs=[item.evidence],
                    taxonomy_version=taxonomy_version,
                )
            )
            continue
        if item.item_type == "language":
            language = LANGUAGE_ALIASES.get(_normalization_key(item.name))
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="language",
                    source_object_id=item.item_id,
                    source_scope=source_scope,
                    raw_text=item.name,
                    canonical_id=language[0] if language else None,
                    canonical_name=language[1] if language else None,
                    vector_text=f"语言能力：{language[1] if language else item.name}",
                    candidate_level=_explicit_candidate_level(item.proficiency),
                    structured_values={
                        "aggregation_key": _skill_aggregation_key(
                            item, language[0] if language else None
                        ),
                        "occurrence_kind": occurrence_kind,
                        "proficiency_explicit": item.proficiency not in (None, "unknown"),
                    },
                    evidence_refs=[item.evidence],
                    taxonomy_version=taxonomy_version,
                    resolution_status="resolved" if language else "unresolved",
                )
            )
            continue
        normalized_item = normalized_by_item.get(item.item_id)
        if normalized_item is None:
            raise ValueError(f"Missing normalized skill for item_id={item.item_id}")
        display_name = normalized_item.canonical_name or item.name
        features.append(
            _feature(
                extraction=extraction,
                feature_type="skill",
                source_object_id=item.item_id,
                source_scope=source_scope,
                raw_text=item.name,
                canonical_id=normalized_item.skill_id,
                canonical_name=normalized_item.canonical_name,
                vector_text=f"技能：{display_name}",
                candidate_level=_explicit_candidate_level(item.proficiency),
                structured_values={
                    "item_type": normalized_item.category_code,
                    "aggregation_key": _skill_aggregation_key(item, normalized_item.skill_id),
                    "occurrence_kind": occurrence_kind,
                    "proficiency_explicit": item.proficiency not in (None, "unknown"),
                },
                evidence_refs=[item.evidence],
                taxonomy_version=taxonomy_version,
                resolution_status=normalized_item.resolution_status,
            )
        )
    return features


def build_cv_match_features(
    extraction: CVExtractionResult,
    normalized: CVNormalizedResult,
    normalization_map: dict[str, Any],
    *,
    as_of_date: date,
) -> CVMatchFeatureResult:
    taxonomy_version = str(normalization_map["version"])
    features = _skill_features(extraction, normalized, taxonomy_version)
    personal = extraction.personal_info
    if personal is not None:
        for scope, value in (
            ("current_location", personal.current_location),
            ("expected_location", personal.expected_location),
        ):
            if value is not None:
                features.append(
                    _feature(
                        extraction=extraction,
                        feature_type="location",
                        source_object_id=scope,
                        source_scope=f"personal_info:{scope}",
                        raw_text=value,
                        vector_text=f"{scope}：{value}",
                        structured_values={"location_kind": scope},
                        evidence_refs=[_field_evidence(personal, scope)],
                        taxonomy_version=taxonomy_version,
                        resolution_status="unresolved",
                    )
                )
        if personal.expected_position is not None:
            classification = classify_role(personal.expected_position, normalization_map)
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="role",
                    source_object_id="expected_position",
                    source_scope="personal_info:expected_position",
                    raw_text=personal.expected_position,
                    canonical_id=classification.get("family_code"),
                    canonical_name=classification.get("family_name"),
                    vector_text=f"期望岗位：{personal.expected_position}",
                    structured_values={"role_kind": "expected"},
                    evidence_refs=[_field_evidence(personal, "expected_position")],
                    taxonomy_version=taxonomy_version,
                    resolution_status=classification["resolution_status"],
                )
            )
        if personal.expected_salary is not None:
            salary = parse_salary(personal.expected_salary)
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="salary",
                    source_object_id="expected_salary",
                    source_scope="personal_info:expected_salary",
                    raw_text=personal.expected_salary,
                    structured_values=salary or {},
                    evidence_refs=[_field_evidence(personal, "expected_salary")],
                    taxonomy_version=taxonomy_version,
                    resolution_status="resolved" if salary is not None else "unresolved",
                )
            )
        if personal.work_status not in (None, "unknown"):
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="work_status",
                    source_object_id="work_status",
                    source_scope="personal_info:work_status",
                    raw_text=personal.work_status,
                    canonical_id=f"WORK_STATUS_{personal.work_status.upper()}",
                    canonical_name=personal.work_status,
                    structured_values={"work_status": personal.work_status},
                    evidence_refs=[_field_evidence(personal, "work_status")],
                    taxonomy_version=taxonomy_version,
                )
            )
        if personal.available_date is not None:
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="availability",
                    source_object_id="available_date",
                    source_scope="personal_info:available_date",
                    raw_text=personal.available_date,
                    structured_values={"available_date": personal.available_date},
                    evidence_refs=[_field_evidence(personal, "available_date")],
                    taxonomy_version=taxonomy_version,
                )
            )

    for entry in extraction.education:
        values = _duration_values(
            entry.date.start if entry.date else None,
            entry.date.end if entry.date else None,
            as_of_date=as_of_date,
        )
        values.update(
            {
                "school": entry.school,
                "major": entry.major,
                "degree": entry.degree,
                "degree_rank": DEGREE_RANK[entry.degree],
            }
        )
        if entry.college is not None:
            values["college"] = entry.college
        if entry.school_tag is not None:
            values["school_tag"] = entry.school_tag
        if entry.gpa is not None:
            values["gpa"] = entry.gpa
        if entry.gpa_scale is not None:
            values["gpa_scale"] = entry.gpa_scale
        if entry.location is not None:
            values["location"] = entry.location
        raw = " | ".join(part for part in (entry.school, entry.major, entry.degree) if part)
        features.append(
            _feature(
                extraction=extraction,
                feature_type="education",
                source_object_id=entry.entry_id,
                source_scope=f"education:{entry.entry_id}",
                raw_text=raw,
                canonical_id=f"DEGREE_{entry.degree.upper()}" if entry.degree != "unknown" else None,
                canonical_name=entry.degree if entry.degree != "unknown" else None,
                vector_text=f"教育经历：{entry.school}；专业：{entry.major}；学历：{entry.degree}",
                structured_values=values,
                evidence_refs=_field_evidences(
                    entry,
                    [
                        name for name in (
                            "school", "major", "degree", "date", "college", "school_tag",
                            "gpa", "gpa_scale", "location",
                        )
                        if getattr(entry, name) is not None
                        and not (name == "degree" and entry.degree == "unknown")
                    ],
                ),
                taxonomy_version=taxonomy_version,
                resolution_status="resolved" if entry.degree != "unknown" else "unresolved",
            )
        )

    if extraction.education:
        highest_rank = max(DEGREE_RANK[entry.degree] for entry in extraction.education)
        highest_entries = [
            entry for entry in extraction.education if DEGREE_RANK[entry.degree] == highest_rank
        ]
        highest_degree = highest_entries[0].degree
        features.append(
            _feature(
                extraction=extraction,
                feature_type="education",
                source_object_id="highest_degree",
                source_scope="education:summary",
                raw_text=highest_degree,
                canonical_id=(
                    f"DEGREE_{highest_degree.upper()}" if highest_degree != "unknown" else None
                ),
                canonical_name=highest_degree if highest_degree != "unknown" else None,
                structured_values={
                    "highest_degree": highest_degree,
                    "highest_degree_rank": highest_rank,
                },
                evidence_refs=[
                    _field_evidence(entry, "degree" if entry.degree != "unknown" else "school")
                    for entry in highest_entries
                ],
                taxonomy_version=taxonomy_version,
                resolution_status="resolved" if highest_degree != "unknown" else "unresolved",
            )
        )

    work_intervals: list[tuple[int, int]] = []
    full_time_intervals: list[tuple[int, int]] = []
    for entry in extraction.work_experience:
        start = entry.date.start if entry.date else None
        end = entry.date.end if entry.date else None
        start_index = _month_index(start, as_of_date=as_of_date)
        end_index = _month_index(end, as_of_date=as_of_date)
        values = _duration_values(start, end, as_of_date=as_of_date)
        values["company"] = entry.company
        if entry.position is not None:
            values["position"] = entry.position
        if entry.department is not None:
            values["department"] = entry.department
        if entry.work_type not in (None, "unknown"):
            values["work_type"] = entry.work_type
        if entry.location is not None:
            values["location"] = entry.location
        if start_index is not None and end_index is not None and end_index >= start_index:
            work_intervals.append((start_index, end_index))
            if entry.work_type == "full_time":
                full_time_intervals.append((start_index, end_index))
        features.append(
            _feature(
                extraction=extraction,
                feature_type="experience",
                source_object_id=entry.entry_id,
                source_scope=f"work_experience:{entry.entry_id}",
                raw_text=" | ".join(
                    part for part in (entry.company, entry.position) if part is not None
                ),
                vector_text=(
                    f"工作经历：{entry.company}；岗位：{entry.position}"
                    if entry.position is not None
                    else f"工作经历：{entry.company}"
                ),
                structured_values=values,
                evidence_refs=_field_evidences(
                    entry,
                    [
                        name for name in (
                            "company", "position", "date", "department", "work_type", "location",
                        )
                        if getattr(entry, name) is not None
                        and not (name == "work_type" and entry.work_type == "unknown")
                    ],
                ),
                taxonomy_version=taxonomy_version,
            )
        )
        if entry.position is not None:
            classification = classify_role(entry.position, normalization_map)
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="role",
                    source_object_id=entry.entry_id,
                    source_scope=f"work_experience:{entry.entry_id}:position",
                    raw_text=entry.position,
                    canonical_id=classification.get("family_code"),
                    canonical_name=classification.get("family_name"),
                    vector_text=f"历史岗位：{entry.position}",
                    structured_values={"role_kind": "historical"},
                    evidence_refs=[_field_evidence(entry, "position")],
                    taxonomy_version=taxonomy_version,
                    resolution_status=classification["resolution_status"],
                )
            )
        if entry.location is not None:
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="location",
                    source_object_id=entry.entry_id,
                    source_scope=f"work_experience:{entry.entry_id}:location",
                    raw_text=entry.location,
                    vector_text=f"工作地点：{entry.location}",
                    structured_values={"location_kind": "work"},
                    evidence_refs=[_field_evidence(entry, "location")],
                    taxonomy_version=taxonomy_version,
                    resolution_status="unresolved",
                )
            )
        for fact_type, facts in (
            ("responsibility", entry.responsibilities),
            ("achievement", entry.achievements),
        ):
            for index, fact in enumerate(facts, start=1):
                features.append(
                    _feature(
                        extraction=extraction,
                        feature_type="task",
                        source_object_id=f"{entry.entry_id}:{fact_type}:{index}",
                        source_scope=f"work_experience:{entry.entry_id}:{fact_type}",
                        raw_text=fact.value,
                        vector_text=f"{entry.position or entry.company}；{fact.value}",
                        structured_values={"task_kind": fact_type},
                        evidence_refs=[
                            _field_evidence(
                                entry, "position" if entry.position is not None else "company"
                            ),
                            fact.evidence,
                        ],
                        taxonomy_version=taxonomy_version,
                    )
                )

    if extraction.work_experience:
        all_months = _merge_month_intervals(work_intervals)
        full_time_months = _merge_month_intervals(full_time_intervals)
        features.append(
            _feature(
                extraction=extraction,
                feature_type="experience",
                source_object_id="work_experience_summary",
                source_scope="work_experience:summary",
                raw_text="工作经验汇总",
                structured_values={
                    "total_work_experience_months": all_months,
                    "full_time_experience_months": full_time_months,
                    "dated_work_entry_count": len(work_intervals),
                    "work_entry_count": len(extraction.work_experience),
                },
                evidence_refs=[
                    _field_evidence(entry, "date" if entry.date is not None else "company")
                    for entry in extraction.work_experience
                ],
                taxonomy_version=taxonomy_version,
                resolution_status=(
                    "resolved" if len(work_intervals) == len(extraction.work_experience) else "unresolved"
                ),
            )
        )

    for project in extraction.project_experience:
        values = _duration_values(
            project.date.start if project.date else None,
            project.date.end if project.date else None,
            as_of_date=as_of_date,
        )
        values["project_name"] = project.name
        if project.affiliation is not None:
            values["affiliation"] = project.affiliation
        if project.role is not None:
            values["project_role"] = project.role
        raw = " | ".join(part for part in (project.name, project.role, project.affiliation) if part)
        features.append(
            _feature(
                extraction=extraction,
                feature_type="experience",
                source_object_id=project.entry_id,
                source_scope=f"project_experience:{project.entry_id}",
                raw_text=raw,
                vector_text=f"项目经历：{raw}",
                structured_values=values,
                evidence_refs=_field_evidences(
                    project,
                    [
                        name for name in ("name", "date", "role", "affiliation")
                        if getattr(project, name) is not None
                    ],
                ),
                taxonomy_version=taxonomy_version,
            )
        )
        project_facts = []
        if project.description is not None:
            project_facts.append(("description", 0, project.description))
        project_facts.extend(
            ("highlight", index, fact) for index, fact in enumerate(project.highlights, start=1)
        )
        for fact_type, index, fact in project_facts:
            features.append(
                _feature(
                    extraction=extraction,
                    feature_type="task",
                    source_object_id=f"{project.entry_id}:{fact_type}:{index}",
                    source_scope=f"project_experience:{project.entry_id}:{fact_type}",
                    raw_text=fact.value,
                    vector_text=f"项目：{project.name}；{fact.value}",
                    structured_values={"task_kind": f"project_{fact_type}"},
                    evidence_refs=[_field_evidence(project, "name"), fact.evidence],
                    taxonomy_version=taxonomy_version,
                )
            )

    for entry in extraction.languages:
        language = LANGUAGE_ALIASES.get(_normalization_key(entry.language))
        features.append(
            _feature(
                extraction=extraction,
                feature_type="language",
                source_object_id=entry.entry_id,
                source_scope=f"languages:{entry.entry_id}",
                raw_text=entry.language,
                canonical_id=language[0] if language else None,
                canonical_name=language[1] if language else None,
                vector_text=f"语言能力：{language[1] if language else entry.language}",
                candidate_level=_explicit_candidate_level(entry.proficiency),
                evidence_refs=[entry.evidence],
                taxonomy_version=taxonomy_version,
                resolution_status="resolved" if language else "unresolved",
            )
        )
    for entry in extraction.certificates:
        values: dict[str, MatchScalar] = {"certificate_kind": entry.kind}
        if entry.issuing_body is not None:
            values["issuing_body"] = entry.issuing_body
        if entry.date is not None:
            values["date"] = entry.date
        features.append(
            _feature(
                extraction=extraction,
                feature_type="certificate",
                source_object_id=entry.entry_id,
                source_scope=f"certificates:{entry.entry_id}",
                raw_text=entry.name,
                vector_text=f"证书：{entry.name}",
                structured_values=values,
                evidence_refs=[entry.evidence],
                taxonomy_version=taxonomy_version,
            )
        )
    for entry in extraction.awards:
        values = {}
        if entry.level is not None:
            values["award_level"] = entry.level
        if entry.issuing_body is not None:
            values["issuing_body"] = entry.issuing_body
        if entry.date is not None:
            values["date"] = entry.date
        features.append(
            _feature(
                extraction=extraction,
                feature_type="award",
                source_object_id=entry.entry_id,
                source_scope=f"awards:{entry.entry_id}",
                raw_text=entry.name,
                vector_text=f"奖项：{entry.name}",
                candidate_level=_explicit_candidate_level(entry.level),
                structured_values=values,
                evidence_refs=[entry.evidence],
                taxonomy_version=taxonomy_version,
            )
        )
    for entry in extraction.self_evaluation:
        features.append(
            _feature(
                extraction=extraction,
                feature_type="self_evaluation",
                source_object_id=entry.entry_id,
                source_scope=f"self_evaluation:{entry.entry_id}",
                raw_text=entry.content,
                vector_text=f"自我评价：{entry.content}",
                evidence_refs=[entry.evidence],
                taxonomy_version=taxonomy_version,
            )
        )

    return CVMatchFeatureResult(
        document_id=extraction.document_id,
        as_of_date=as_of_date.isoformat(),
        taxonomy_version=taxonomy_version,
        derivation_version=MATCH_FEATURE_DERIVATION_VERSION,
        features=features,
    )
