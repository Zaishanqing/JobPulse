from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from app.domain.discovery import (
    AlgorithmCluster,
    GeneratedDefinition,
    GeneratedSkill,
    JDSnapshot,
)
from app.domain.values import FrozenDict, freeze


def _normalized(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _similar(left: str, right: str) -> bool:
    left_value, right_value = _normalized(left), _normalized(right)
    if not left_value or not right_value:
        return False
    if left_value in right_value or right_value in left_value:
        return (
            min(len(left_value), len(right_value)) / max(len(left_value), len(right_value)) >= 0.65
        )
    return SequenceMatcher(None, left_value, right_value).ratio() >= 0.72


def _enterprise(snapshot: JDSnapshot) -> str | None:
    for key in ("enterprise_id", "company_id", "company_name"):
        value = snapshot.structured_data.extensions.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _source_platform(snapshot: JDSnapshot) -> str | None:
    value = snapshot.structured_data.extensions.get("source_platform")
    return str(value).strip().casefold() if value is not None and str(value).strip() else None


def _evidence_id(snapshot: JDSnapshot) -> str:
    return ":".join(
        (
            snapshot.source_fact_id,
            snapshot.source_fact_version,
        )
    )


def _evidence_record(snapshot: JDSnapshot, snippet: str, field_type: str) -> dict[str, object]:
    return {
        "evidence_id": _evidence_id(snapshot),
        "source_jd_id": snapshot.jd_id,
        "original_text_snippet": snippet,
        "field_type": field_type,
        "data_source": snapshot.source_name or "unavailable",
        "window_id": snapshot.window_id,
        "locator": {
            "source_fact_id": snapshot.source_fact_id,
            "source_fact_version": snapshot.source_fact_version,
            "structured_path": field_type,
        },
    }


def _support(members: dict[str, JDSnapshot], cluster: AlgorithmCluster) -> dict[str, object]:
    cluster_sources = {value for item in cluster.members if (value := _source_platform(item))}
    sources = {value for item in members.values() if (value := _source_platform(item))}
    cluster_enterprises = {value for item in cluster.members if (value := _enterprise(item))}
    enterprises = {value for item in members.values() if (value := _enterprise(item))}
    jd_ratio = len(members) / max(len(cluster.members), 1)
    source_ratio = len(sources) / max(len(cluster_sources), 1)
    if cluster_enterprises:
        confidence = (
            0.5 * jd_ratio
            + 0.3 * source_ratio
            + 0.2 * (len(enterprises) / len(cluster_enterprises))
        )
    else:
        confidence = 0.625 * jd_ratio + 0.375 * source_ratio
    return {
        "evidence_ids": sorted(_evidence_id(item) for item in members.values()),
        "support_jd_count": len(members),
        "support_enterprise_count": len(enterprises),
        "support_source_count": len(sources),
        "confidence": round(min(max(confidence, 0.0), 1.0), 4),
    }


def _field(
    content: object,
    items: list[dict[str, object]],
    cluster: AlgorithmCluster,
    method: str,
    missing_reason: str | None = None,
    suggested_data: tuple[str, ...] = (),
) -> dict[str, object]:
    supporting: dict[str, JDSnapshot] = {}
    for index, item in enumerate(items):
        supporting.update(item.get("_members", {}))  # internal aggregation detail
        items[index] = {key: value for key, value in item.items() if key != "_members"}
    result = {
        "content": content,
        **_support(supporting, cluster),
        "generation_method": method,
        "items": items,
        "missing_reason": missing_reason,
        "suggested_data": list(suggested_data),
    }
    if not items:
        result["confidence"] = 0.0
    return result


def _text_groups(
    values: list[tuple[str, JDSnapshot]],
) -> list[tuple[str, dict[str, JDSnapshot]]]:
    groups: list[tuple[str, dict[str, JDSnapshot]]] = []
    for text, member in sorted(values, key=lambda item: (_normalized(item[0]), item[1].jd_id)):
        if not text.strip():
            continue
        for index, (representative, members) in enumerate(groups):
            if _similar(text, representative):
                members[member.jd_id] = member
                if len(text.strip()) < len(representative):
                    groups[index] = (text.strip(), members)
                break
        else:
            groups.append((text.strip(), {member.jd_id: member}))
    return sorted(groups, key=lambda item: (-len(item[1]), _normalized(item[0])))


def _item(
    content: str,
    members: dict[str, JDSnapshot],
    cluster: AlgorithmCluster,
    method: str,
    field_type: str = "claim",
    evidence_snippet: str | None = None,
    **extra: object,
) -> dict[str, object]:
    return {
        "content": content,
        **_support(members, cluster),
        "generation_method": method,
        "evidence": tuple(
            _evidence_record(member, evidence_snippet or content, field_type)
            for member in sorted(members.values(), key=lambda value: value.jd_id)
        ),
        "_members": members,
        **extra,
    }


def generate_evidence_definition(
    cluster: AlgorithmCluster,
    reference_skill_sets: list[set[str]] | None = None,
) -> GeneratedDefinition:
    titles = _text_groups([(item.title, item) for item in cluster.members])
    if titles:
        position_name = titles[0][0]
        name_items = [
            _item(
                position_name,
                titles[0][1],
                cluster,
                "evidence_extractive",
                field_type="position_name",
            )
        ]
        name_missing = None
    else:
        position_name = ""
        name_items = []
        name_missing = "no non-empty position title is present in the cluster"

    responsibility_groups = _text_groups(
        [
            (responsibility, member)
            for member in cluster.members
            for responsibility in member.structured_data.responsibilities
        ]
    )
    minimum_responsibility_support = (
        1 if len(cluster.members) == 1 else max(2, math.ceil(len(cluster.members) * 0.4))
    )
    responsibility_groups = [
        item for item in responsibility_groups if len(item[1]) >= minimum_responsibility_support
    ][:8]
    responsibility_items = [
        _item(
            text,
            members,
            cluster,
            "evidence_extractive",
            field_type="responsibility",
        )
        for text, members in responsibility_groups
    ]
    core_responsibilities = tuple(item[0] for item in responsibility_groups)
    if not titles and responsibility_groups:
        position_name = responsibility_groups[0][0]
        name_items = [
            _item(
                position_name,
                responsibility_groups[0][1],
                cluster,
                "evidence_extractive",
                field_type="position_name",
            )
        ]
        name_missing = None

    skill_members: dict[str, dict[str, JDSnapshot]] = defaultdict(dict)
    required_members: dict[str, dict[str, JDSnapshot]] = defaultdict(dict)
    display_names: dict[str, Counter[str]] = defaultdict(Counter)
    for member in cluster.members:
        structured = member.structured_data
        for skill in structured.required_skills + structured.bonus_skills:
            if skill.identity and str(skill.identity).strip():
                key = str(skill.identity).strip().casefold()
                skill_members[key][member.jd_id] = member
                display_names[key][str(skill.identity).strip()] += 1
        for skill in structured.required_skills:
            if skill.identity and str(skill.identity).strip():
                required_members[str(skill.identity).strip().casefold()][member.jd_id] = member
    responsibility_members = (
        set().union(*(members for _, members in responsibility_groups))
        if responsibility_groups
        else set()
    )
    skill_scores: dict[str, tuple[float, float, float, float]] = {}
    keys = sorted(skill_members)
    for key in keys:
        members = skill_members[key]
        support_rate = len(members) / max(len(cluster.members), 1)
        responsibility_association = len(set(members) & responsibility_members) / max(
            len(members), 1
        )
        overlaps = [
            len(set(members) & set(skill_members[other]))
            / len(set(members) | set(skill_members[other]))
            for other in keys
            if other != key
        ]
        distinction = 1.0 - (sum(overlaps) / len(overlaps) if overlaps else 0.0)
        declared_required_rate = len(required_members[key]) / max(len(members), 1)
        score = (
            0.4 * support_rate
            + 0.25 * responsibility_association
            + 0.2 * distinction
            + 0.15 * declared_required_rate
        )
        skill_scores[key] = (score, support_rate, responsibility_association, distinction)
    required_keys = [
        key
        for key in keys
        if len(required_members[key]) / max(len(cluster.members), 1) >= 0.5
        and skill_scores[key][1] >= 0.5
        and skill_scores[key][0] >= 0.5
    ]
    required_keys.sort(key=lambda key: (-skill_scores[key][0], key))
    minimum_required_frequency = min((skill_scores[key][1] for key in required_keys), default=1.0)
    bonus_keys = [
        key
        for key in keys
        if required_keys
        and key not in required_keys
        and skill_scores[key][1] < minimum_required_frequency
        and skill_scores[key][3] >= 0.3
        and len(
            {value for member in skill_members[key].values() if (value := _source_platform(member))}
        )
        >= 2
    ]
    bonus_keys.sort(key=lambda key: (-skill_scores[key][0], key))

    def skill_item(key: str) -> dict[str, object]:
        score, rate, association, distinction = skill_scores[key]
        value = display_names[key].most_common(1)[0][0]
        return _item(
            value,
            skill_members[key],
            cluster,
            "rule_based_candidate",
            field_type="skill",
            evidence_snippet=value,
            support_rate=round(rate, 4),
            responsibility_association=round(association, 4),
            cluster_distinctiveness=round(distinction, 4),
            selection_score=round(score, 4),
        )

    required_items = [skill_item(key) for key in required_keys[:12]]
    bonus_items = [skill_item(key) for key in bonus_keys[:12]]
    required_skills = tuple(
        GeneratedSkill(
            str(item["content"]),
            key,
            float(item["selection_score"]),
        )
        for key, item in zip(required_keys[:12], required_items, strict=True)
    )
    bonus_skills = tuple(
        GeneratedSkill(
            str(item["content"]),
            key,
            float(item["selection_score"]),
        )
        for key, item in zip(bonus_keys[:12], bonus_items, strict=True)
    )

    scenario_groups = _text_groups(
        [
            (value, member)
            for member in cluster.members
            for value in (
                *member.structured_data.business_scenarios,
                *((member.structured_data.industry,) if member.structured_data.industry else ()),
            )
        ]
    )
    scenario_items = [
        _item(
            text,
            members,
            cluster,
            "evidence_extractive",
            field_type="industry_scenario",
        )
        for text, members in scenario_groups[:12]
    ]
    industry_scenarios = tuple(text for text, _ in scenario_groups[:12])

    summary = core_responsibilities[0] if core_responsibilities else ""
    summary_items = responsibility_items[:1]
    references = [set(value) for value in (reference_skill_sets or []) if value]
    nearest = max(
        references,
        key=lambda reference: (
            len(set(required_keys) & reference) / max(len(set(required_keys) | reference), 1)
        ),
        default=set(),
    )
    distinguishing_keys = [key for key in required_keys if key not in nearest]
    skill_items_by_key = {
        key: item for key, item in zip(required_keys[:12], required_items, strict=True)
    }
    distinguishing_items = []
    distinguishing_features = []
    for key in distinguishing_keys:
        source_item = skill_items_by_key.get(key)
        if source_item is None:
            continue
        label = str(source_item["content"])
        claim = f"相对最近标准岗位新增技能特征：{label}"
        distinguishing_features.append(claim)
        distinguishing_items.append(
            {
                **source_item,
                "content": claim,
                "comparison_basis": "nearest_standard_position_skill_difference",
            }
        )
    enterprise_counts = Counter(
        value for member in cluster.members if (value := _enterprise(member))
    )
    enterprise_items = [
        _item(
            enterprise,
            {
                member.jd_id: member
                for member in cluster.members
                if _enterprise(member) == enterprise
            },
            cluster,
            "member_enterprise_distribution",
            field_type="enterprise",
        )
        for enterprise in sorted(enterprise_counts)
    ]
    trajectory_counts = Counter(member.window_id for member in cluster.members)
    growth_trajectory = tuple(
        FrozenDict({"window_id": window_id, "member_count": trajectory_counts[window_id]})
        for window_id in sorted(trajectory_counts)
    )
    trajectory_items = [
        _item(
            f"{window_id}:{trajectory_counts[window_id]}",
            {member.jd_id: member for member in cluster.members if member.window_id == window_id},
            cluster,
            "window_member_count",
            field_type="growth_trajectory",
        )
        for window_id in sorted(trajectory_counts)
    ]

    fields = {
        "position_name": _field(
            position_name,
            name_items,
            cluster,
            "evidence_extractive",
            name_missing,
            ("position_title",) if name_missing else (),
        ),
        "core_responsibilities": _field(
            list(core_responsibilities),
            responsibility_items,
            cluster,
            "evidence_extractive",
            None if responsibility_items else "responsibilities lack repeated factual support",
            () if responsibility_items else ("responsibilities from at least two JDs",),
        ),
        "required_skills": _field(
            [item.raw_skill for item in required_skills],
            required_items,
            cluster,
            "rule_based_candidate",
            None if required_items else "no skill meets required-skill support thresholds",
            () if required_items else ("structured required_skills",),
        ),
        "bonus_skills": _field(
            [item.raw_skill for item in bonus_skills],
            bonus_items,
            cluster,
            "rule_based_candidate",
            None
            if bonus_items
            else "no lower-frequency distinctive skill has multi-source support",
            () if bonus_items else ("bonus skills supported by at least two sources",),
        ),
        "industry_scenarios": _field(
            list(industry_scenarios),
            scenario_items,
            cluster,
            "evidence_extractive",
            None
            if scenario_items
            else "structured industry and business scenario fields are unavailable",
            () if scenario_items else ("business_scenarios or industry",),
        ),
        "position_summary": _field(
            summary,
            list(summary_items),
            cluster,
            "evidence_extractive",
            None if summary_items else "no evidence-backed responsibility is available",
        ),
        "distinguishing_features": _field(
            list(distinguishing_features),
            distinguishing_items,
            cluster,
            "standard_position_skill_difference",
            None
            if distinguishing_items
            else "no evidence-backed difference from the nearest standard position",
        ),
        "representative_enterprises": _field(
            dict(sorted(enterprise_counts.items())),
            enterprise_items,
            cluster,
            "member_enterprise_distribution",
            None if enterprise_counts else "enterprise identity is unavailable",
        ),
        "growth_trajectory": _field(
            [dict(item) for item in growth_trajectory],
            trajectory_items,
            cluster,
            "window_member_count",
        ),
    }
    frozen = freeze(fields)
    if not isinstance(frozen, FrozenDict):
        raise TypeError("definition evidence must be a JSON object")
    return GeneratedDefinition(
        position_name=position_name,
        core_responsibilities=core_responsibilities,
        required_skills=required_skills,
        bonus_skills=bonus_skills,
        industry_scenarios=industry_scenarios,
        generation_mode="rule_based_candidate",
        field_evidence=frozen,
        position_summary=summary,
        distinguishing_features=tuple(distinguishing_features),
        representative_enterprises=FrozenDict(dict(sorted(enterprise_counts.items()))),
        growth_trajectory=growth_trajectory,
    )
