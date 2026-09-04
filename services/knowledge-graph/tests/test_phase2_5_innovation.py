from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.domain.contract_compatibility import (
    NormalizedRequirement,
    NormalizedSkill,
    SourceRequirement,
    audit_requirement_compatibility,
)
from app.domain.dependency_analysis import (
    DependencyPolicy,
    RequirementContext,
    analyze_skill_dependencies,
)
from app.domain.projections import build_graph_projection, verify_graph_projection
from app.domain.temporal_analysis import (
    ComparabilityContext,
    VersionChangeInputs,
    WatermarkSourceFact,
    attribute_version_change,
    compare_build_watermarks,
    create_build_input_watermark,
)
from app.domain.traceability import (
    ClaimEvidenceRef,
    MappingCandidate,
    MappingAffectedContext,
    MappingCandidateSignals,
    MappingPriorityWeights,
    MappingReviewDecision,
    RelationClaim,
    decide_relation_claim,
    rank_mapping_candidate,
    validate_mapping_review,
)


FIXTURE = Path(__file__).parent / "fixtures" / "unvalidated_extraction_jd_000001.json"


def _compatibility_fixture():
    payload = json.loads(FIXTURE.read_text("utf-8"))
    source = tuple(
        SourceRequirement(
            requirement_id=item["requirement_id"],
            kind=item["kind"],
            modality=item["modality"],
            skill_names=tuple(item["skill_names"]),
            evidence_source_id=item["evidence_source_id"],
            evidence_quote=item["evidence_quote"],
        )
        for item in payload["source_requirements"]
    )
    normalized = tuple(
        NormalizedRequirement(
            requirement_id=item["requirement_id"],
            kind=item["kind"],
            modality=item["modality"],
            skills=tuple(
                NormalizedSkill(
                    skill["source_name"],
                    skill["resolution_status"],
                    skill["skill_id"],
                )
                for skill in item["skills"]
            ),
        )
        for item in payload["normalized_requirements"]
    )
    return payload, source, normalized


def _watermark(*, catalog: str = "catalog-1", coverage: float = 1.0):
    return create_build_input_watermark(
        source_facts=(
            WatermarkSourceFact("published_fact", "fact-2", "1", "source-v2"),
            WatermarkSourceFact("published_fact", "fact-1", "2", "source-v1"),
        ),
        observation_window_start="2026-01-01T00:00:00Z",
        observation_window_end="2026-07-01T00:00:00Z",
        catalog_snapshot_id=catalog,
        catalog_source_version=("catalog-v1" if catalog == "catalog-1" else "catalog-v2"),
        validation_policy_version="validation.v1",
        mapping_policy_version="mapping.v1",
        aggregation_algorithm_version="aggregation.v1",
        normalized_config={"minimum_samples": 5, "candidate_edges": False},
        input_coverage=coverage,
    )


def _claim(*, kind="observed", graph_version_id=7):
    quote = "熟练使用 Python"
    return RelationClaim(
        claim_id="claim-1",
        support_id=11,
        subject_id="POSITION_AI",
        predicate="REQUIRES_SKILL",
        object_id="LANG_PYTHON",
        claim_kind=kind,
        source_kind="published_fact",
        source_fact_id="fact-1",
        source_fact_version="2",
        requirement_id="req-1",
        evidence=(ClaimEvidenceRef(11, "src-1", quote, 0, len(quote), True),),
        validation_lineage_lineage_version="validation-report-v1",
        catalog_snapshot_lineage_version="catalog-v1",
        mapping_policy_version="mapping.v1",
        observed_at="2026-06-01T00:00:00Z",
        graph_version_id=graph_version_id,
    )


def _mapping_candidate():
    return MappingCandidate(
        candidate_id="map-1",
        source_expression="NeMo Framework",
        proposed_skill_id="FRAMEWORK_NEMO",
        signals=MappingCandidateSignals(0.8, 0.9, 0.4, 0.6, 0.3),
        model_version="embedding.v1",
        index_version="catalog-index.v1",
        mapping_policy_version="mapping.v1",
        affected_contexts=(MappingAffectedContext("fact-1", "req_004"),),
    )


def _dependency_contexts():
    contexts = []
    for index in range(12):
        advanced = index < 6
        contexts.append(
            RequirementContext(
                context_id=f"context-{index:02d}",
                document_id=f"jd-{index:02d}",
                requirement_id=f"req-{index:02d}",
                skill_ids=frozenset(
                    {"LANG_PYTHON", "AI_TRANSFORMER" if advanced else "TOOL_GIT"}
                ),
                source_name=f"source-{index % 4}",
                enterprise_id=f"enterprise-{index}",
                industry="software",
                region="cn-south" if index % 2 else "cn-east",
                time_slice="2026-H1" if index % 2 else "2025-H2",
                template_family_id=f"template-{index}",
                evidence_ids=(100 + index,),
            )
        )
    contexts.append(replace(contexts[0], context_id="duplicate-context"))
    return tuple(contexts)


def _dependency_analysis():
    return analyze_skill_dependencies(
        _dependency_contexts(),
        DependencyPolicy(
            minimum_joint_support=4,
            minimum_conditional_probability=0.8,
            minimum_source_diversity=3,
            minimum_enterprise_diversity=3,
            maximum_enterprise_share=0.4,
            bootstrap_iterations=300,
            confidence_level=0.95,
            minimum_stable_slices=2,
        ),
    )


def test_phase2_real_extraction_fixture_is_explicitly_non_authoritative():
    payload, source, normalized = _compatibility_fixture()
    assert payload["fixture_provenance"]["validation_status"] == "not_provided"
    assert payload["fixture_provenance"]["authority_allowed"] is False
    audit = audit_requirement_compatibility(source, normalized)
    assert audit.accepted is True
    assert audit.matched_requirement_ids == ("req_003", "req_004", "req_005")
    assert audit.unresolved_source_names == ("NeMo Framework",)


def test_phase2_requirement_id_audit_rejects_cross_kind_flattening():
    _, source, normalized = _compatibility_fixture()
    broken = tuple(
        replace(
            item,
            skills=(NormalizedSkill("本科", "resolved", "FAKE_SKILL"),),
        )
        if item.requirement_id == "req_005"
        else item
        for item in normalized
    )
    audit = audit_requirement_compatibility(source, broken)
    assert audit.accepted is False
    assert {issue.reject_code for issue in audit.issues} == {
        "NON_SKILL_REQUIREMENT_HAS_SKILLS"
    }


def test_phase2_claim_boundary_and_active_mapping_are_explicit():
    decision = decide_relation_claim(_claim())
    assert decision.accepted is True
    assert decision.lineage_version == "claim-1"

    forbidden = decide_relation_claim(_claim(kind="inferred_candidate"))
    assert forbidden.accepted is False
    assert forbidden.rejection is not None
    assert forbidden.rejection.error_code == "CANDIDATE_AUTHORITY_BOUNDARY_VIOLATION"

    ranked = rank_mapping_candidate(
        _mapping_candidate(),
        MappingPriorityWeights(0.30, 0.25, 0.20, 0.15, 0.10),
    )
    assert ranked.priority == 0.665
    no_match = MappingReviewDecision(
        candidate_id="map-1",
        decision="no_match",
        reviewer_id=3,
        reason="目录中没有等价标准技能",
        policy_version="mapping.v1",
        decided_at="2026-07-24T00:00:00Z",
        effective_scope="catalog-1",
    )
    validate_mapping_review(no_match)


def test_phase3_watermark_is_order_independent_and_comparability_is_gated():
    first = _watermark()
    second = create_build_input_watermark(
        source_facts=tuple(reversed(first.source_facts)),
        observation_window_start=first.observation_window_start,
        observation_window_end=first.observation_window_end,
        catalog_snapshot_id=first.catalog_snapshot_id,
        catalog_source_version=first.catalog_source_version,
        validation_policy_version=first.validation_policy_version,
        mapping_policy_version=first.mapping_policy_version,
        aggregation_algorithm_version=first.aggregation_algorithm_version,
        normalized_config=first.normalized_config,
        input_coverage=first.input_coverage,
    )
    assert first.lineage_version == second.lineage_version
    blocked = compare_build_watermarks(
        first, _watermark(catalog="catalog-2", coverage=0.8), ComparabilityContext()
    )
    assert blocked.comparable is False
    assert blocked.reasons == (
        "catalog_crosswalk_required",
        "input_coverage_below_threshold",
    )
    allowed = compare_build_watermarks(
        first,
        _watermark(catalog="catalog-2"),
        ComparabilityContext(approved_catalog_crosswalk=True),
    )
    assert allowed.comparable is True

    attribution = attribute_version_change(VersionChangeInputs(0.12, 0.05, 0.02, 0.01, 0.03))
    assert attribution.unexplained_residual == 0.01


def test_phase4_dependency_is_candidate_only_deduplicated_and_reproducible():
    first = _dependency_analysis()
    second = _dependency_analysis()
    assert first == second
    assert first.excluded_contexts[0].reason == "duplicate_enterprise_template"
    candidate = next(
        item
        for item in first.candidates
        if item.prerequisite_skill_id == "LANG_PYTHON"
        and item.advanced_skill_id == "AI_TRANSFORMER"
    )
    assert candidate.claim_kind == "inferred_candidate"
    assert candidate.dependency_score == 0.5
    assert candidate.bootstrap_lower > 0
    assert candidate.stable_slices == ("2025-H2", "2026-H1")


def test_phase5_projection_is_rebuildable_and_candidate_edges_stay_isolated():
    dependency = next(
        item
        for item in _dependency_analysis().candidates
        if item.prerequisite_skill_id == "LANG_PYTHON"
        and item.advanced_skill_id == "AI_TRANSFORMER"
    )
    first = build_graph_projection(
        projection_version="trace-skill-projection.v1",
        graph_version_id=7,
        source_version="graph-v1",
        watermark_lineage_version=_watermark().lineage_version,
        claims=(_claim(),),
        mapping_candidates=(_mapping_candidate(),),
        dependency_candidates=(dependency,),
    )
    second = build_graph_projection(
        projection_version="trace-skill-projection.v1",
        graph_version_id=7,
        source_version="graph-v1",
        watermark_lineage_version=_watermark().lineage_version,
        claims=(_claim(),),
        mapping_candidates=(_mapping_candidate(),),
        dependency_candidates=(dependency,),
    )
    assert first == second
    assert verify_graph_projection(first) is True
    assert all(
        edge.plane == "candidate"
        for edge in first.edges
        if edge.edge_type in {"PROPOSES_MAPPING", "STATISTICALLY_SUPPORTS"}
    )
    assert all(
        edge.plane == "authoritative"
        for edge in first.edges
        if edge.edge_type in {"HAS_CLAIM", "REQUIRES_SKILL", "SUPPORTED_BY"}
    )


def test_phase5_projection_rejects_candidate_smuggled_as_authority():
    with pytest.raises(ValueError, match="CANDIDATE_AUTHORITY_BOUNDARY_VIOLATION"):
        build_graph_projection(
            projection_version="trace-skill-projection.v1",
            graph_version_id=7,
            source_version="graph-v1",
            watermark_lineage_version=_watermark().lineage_version,
            claims=(_claim(kind="inferred_candidate"),),
        )


def test_phase5_unvalidated_observation_has_a_distinct_projection_plane():
    projection = build_graph_projection(
        projection_version="trace-skill-projection.v1",
        graph_version_id=7,
        source_version="graph-v1",
        watermark_lineage_version=_watermark().lineage_version,
        claims=(
            replace(
                _claim(),
                source_kind="legacy_local",
                validation_lineage_lineage_version=None,
            ),
        ),
    )
    assert {
        edge.plane
        for edge in projection.edges
        if edge.edge_type in {"HAS_CLAIM", "REQUIRES_SKILL", "SUPPORTED_BY"}
    } == {"observed_unvalidated"}
