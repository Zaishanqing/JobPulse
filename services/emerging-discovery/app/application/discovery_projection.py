from __future__ import annotations

import math
from uuid import uuid4

from app.application.discovery_mapping import algorithm_metadata_contract, snapshot_contract
from app.domain.discovery import (
    AlgorithmOutput,
    ClusterFeatureSummary,
    JDSnapshot,
    PositionReference,
)
from app.domain.lineage import ClusterLineageSpec
from app.domain.values import FrozenDict
from app.ports.records import (
    ClusterAggregate,
    ClusterAssessmentRecord,
    ClusterMembershipRecord,
    SnapshotRecord,
)


def fact_identity(snapshot: JDSnapshot) -> str:
    return "\x1f".join(
        (
            snapshot.source_fact_id,
            snapshot.source_fact_version,
        )
    )


def _distribution(values: list[str | None]) -> FrozenDict | str:
    available = [value.strip() for value in values if value and value.strip()]
    if len(available) != len(values):
        return "unavailable"
    return FrozenDict({value: available.count(value) for value in sorted(set(available))})


def _enterprise(snapshot: JDSnapshot) -> str | None:
    for key in ("enterprise_id", "company_id", "company_name"):
        value = snapshot.structured_data.extensions.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _standard_position_comparison(
    candidate_skills: tuple[str, ...],
    references: tuple[PositionReference, ...],
) -> FrozenDict:
    candidates = {item.casefold() for item in candidate_skills if item}
    comparisons = []
    for reference in references:
        standard: set[str] = set()
        for skill in reference.required_skills:
            if isinstance(skill, dict):
                identity = skill.get("normalized_skill_id") or skill.get("raw_skill")
            else:
                identity = skill.identity
            if identity:
                standard.add(str(identity).casefold())
        if not standard:
            continue
        union = candidates | standard
        similarity = len(candidates & standard) / len(union) if union else 0.0
        comparisons.append((similarity, reference.position_id, standard))
    if not comparisons:
        return FrozenDict(
            {
                "nearest_standard_position": "unavailable",
                "comprehensive_similarity": "unavailable",
                "shared_skills": "unavailable",
                "new_skills": tuple(sorted(candidates)),
                "shared_responsibilities": "unavailable",
                "new_responsibilities": "unavailable",
                "possible_alias": "unavailable",
                "possible_industry_variant": "unavailable",
                "possible_tool_stack_variant": "unavailable",
                "reason": "position reference skills are unavailable",
            }
        )
    similarity, position_id, standard = max(comparisons, key=lambda value: (value[0], value[1]))
    shared = sorted(candidates & standard)
    new = sorted(candidates - standard)
    possible_alias = similarity >= 0.85 and not new
    possible_tool_variant = similarity >= 0.5 and bool(new)
    if possible_alias:
        reason = "skill overlap is very high and no material new skill is present"
    elif possible_tool_variant:
        reason = "core skills overlap, while additional skills mainly indicate a tool-stack variant"
    else:
        reason = "skill difference is material relative to the nearest standard position"
    return FrozenDict(
        {
            "nearest_standard_position": position_id,
            "comprehensive_similarity": round(similarity, 6),
            "shared_skills": tuple(shared),
            "new_skills": tuple(new),
            "shared_responsibilities": "unavailable",
            "new_responsibilities": "unavailable",
            "possible_alias": possible_alias,
            "possible_industry_variant": "unavailable",
            "possible_tool_stack_variant": possible_tool_variant,
            "reason": reason,
        }
    )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def build_snapshot_records(run_id: str, snapshots: tuple[JDSnapshot, ...]) -> list[SnapshotRecord]:
    return [
        SnapshotRecord(
            id=str(uuid4()),
            run_id=run_id,
            source_jd_id=item.jd_id,
            window_id=item.window_id,
            input_version=item.source_fact_version,
            schema_version=item.schema_version,
            payload=snapshot_contract(item),
        )
        for item in snapshots
    ]


def build_cluster_aggregates(
    run_id: str,
    output: AlgorithmOutput,
    snapshot_records: list[SnapshotRecord],
    position_references: tuple[PositionReference, ...] = (),
    lineage_window_id: str = "unavailable",
) -> tuple[list[ClusterAggregate], list[ClusterLineageSpec]]:
    by_jd = {item.source_jd_id: item for item in snapshot_records}
    aggregates: list[ClusterAggregate] = []
    current_specs: list[ClusterLineageSpec] = []
    for item in output.clusters:
        cluster_id = str(uuid4())
        members = item.members
        assessment = item.assessment
        generated_definition = item.generated_definition
        if assessment is None or generated_definition is None:
            raise ValueError("algorithm cluster is missing assessment or generated definition")
        evidence = FrozenDict(
            {
                **assessment.evidence_summary,
                "evidence_jd_ids": sorted(member.jd_id for member in members),
                "input_snapshot_versions": sorted(
                    by_jd[member.jd_id].input_version for member in members
                ),
                **dict(algorithm_metadata_contract(output.metadata)),
                "formula_version": assessment.formula_version,
                "weights": dict(assessment.weights),
                "thresholds": dict(assessment.thresholds),
                "dimension_inputs": vars(assessment.dimensions),
                "cluster_explainability": FrozenDict(
                    {
                        "member_fact_identities": tuple(
                            FrozenDict(
                                {
                                    "source_fact_id": member.source_fact_id,
                                    "source_fact_version": member.source_fact_version,
                                }
                            )
                            for member in members
                        ),
                        "core_skills": tuple(item.core_skills),
                        "title_distribution": _distribution([member.title for member in members]),
                        "enterprise_distribution": _distribution(
                            [_enterprise(member) for member in members]
                        ),
                        "source_distribution": _distribution(
                            [member.source_name for member in members]
                        ),
                    }
                ),
                "standard_position_comparison": _standard_position_comparison(
                    item.core_skills,
                    position_references,
                ),
                "diagnostic_features": FrozenDict(
                    {
                        **dict(assessment.evidence_summary.get("diagnostic_features", {})),
                        "standard_position_comparison": FrozenDict(
                            {
                                **_standard_position_comparison(
                                    item.core_skills,
                                    position_references,
                                ),
                                "scored": False,
                            }
                        ),
                    }
                ),
            }
        )
        aggregates.append(
            ClusterAggregate(
                id=cluster_id,
                run_id=run_id,
                cluster_key=item.key,
                cluster_name=item.cluster_name,
                sample_count=len(members),
                core_skills=item.core_skills,
                representative_titles=tuple(member.title for member in members[:5]),
                representative_members=tuple(
                    FrozenDict(
                        {
                            "source_jd_id": member.jd_id,
                            "title": member.title,
                            "window_id": member.window_id,
                            "input_version": member.source_fact_version,
                        }
                    )
                    for member in members[:5]
                ),
                core_responsibilities=item.core_responsibilities,
                semantic_centroid=item.semantic_centroid,
                algorithm_sources=item.algorithm_sources,
                merge_basis=item.merge_basis,
                stability_score=item.stability_score,
                growth_score=assessment.dimensions.cluster_growth_rate,
                distance_from_existing_positions=assessment.dimensions.distance_from_existing_positions,
                feature_summary=ClusterFeatureSummary(output.metadata, item.centroid),
                memberships=tuple(
                    ClusterMembershipRecord(
                        id=str(uuid4()),
                        input_snapshot_id=by_jd[member.jd_id].id,
                        membership_score=round(
                            min(
                                1.0,
                                max(
                                    0.0,
                                    _cosine(
                                        item.centroid,
                                        output.embedding_for(member.jd_id),
                                    ),
                                ),
                            ),
                            6,
                        ),
                    )
                    for member in members
                ),
                assessment=ClusterAssessmentRecord(
                    id=str(uuid4()),
                    result=assessment,
                    evidence_package=evidence,
                    generated_definition=generated_definition,
                ),
            )
        )
        current_specs.append(
            ClusterLineageSpec(
                cluster_id,
                frozenset(fact_identity(member) for member in members),
                item.centroid,
                frozenset(str(skill).casefold() for skill in item.core_skills),
                lineage_window_id,
                item.key,
            )
        )
    return aggregates, current_specs
