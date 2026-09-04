from __future__ import annotations

from datetime import date

import pytest

from app.contexts.emerging_positions.domain import (
    EmergingCandidate,
    EmergingRecord,
)
from app.contexts.evidence_independence.adapters import (
    EvidenceSupportScoreConclusion,
)
from app.contexts.evidence_independence.application import (
    build_certificate,
    build_summary,
)
from app.contexts.evidence_independence.contracts import (
    EvidenceRecord,
    IndependenceRequest,
)
from app.contexts.market_intelligence import TrendReportRecord
from app.domain.trend_analysis import (
    SkillWeightDistribution,
    TrendGraphSnapshot,
    TrendSkill,
)
from app.contexts.insight_cards import (
    assemble_insight_card,
    emerging_card_source,
    evolution_event_card_source,
    matching_what_if_card_source,
    trend_report_card_source,
)


def _candidate(status: str = "pending_review", **overrides) -> EmergingCandidate:
    values = {
        "candidate_id": "emerging-1",
        "cluster_id": "cluster-1",
        "position_name": "AI Prompt Engineer",
        "core_responsibilities": ("design prompts",),
        "required_skills": ({"skill_id": "s1", "level": "required"},),
        "bonus_skills": ({"skill_id": "s2", "level": "bonus"},),
        "industry_scenarios": ("internet",),
        "germination_score": 0.9,
        "score_dimensions": {"novelty": 0.8},
        "evidence_jd_ids": ("jd-1", "jd-2", "jd-3"),
        "status": status,
    }
    values.update(overrides)
    return EmergingCandidate.create(**values)


def _record(evidence_id: str, **overrides) -> EvidenceRecord:
    values = {
        "evidence_id": evidence_id,
        "subject_ref": "emerging-1",
        "source_id": f"source-{evidence_id}",
        "enterprise_id": f"ent-{evidence_id}",
        "template_cluster_id": f"tpl-{evidence_id}",
        "position_id": "pos-1",
        "published_at": date(2026, 7, 1),
        "release_id": "release-1",
    }
    values.update(overrides)
    return EvidenceRecord(**values)


def _summary() -> object:
    records = [
        _record("jd-1"),
        _record("jd-2"),
        _record("jd-3"),
    ]
    return build_summary(
        records,
        IndependenceRequest(
            subject_ref="emerging-1",
            release_id="release-1",
            coverage_status="covered",
        ),
    )


def test_emerging_card_maps_evidence_n_eff_and_used_evidence() -> None:
    record = EmergingRecord(candidate=_candidate(), created_at=None, updated_at=None)
    summary = _summary()
    card = assemble_insight_card(
        emerging_card_source(
            record,
            summary=summary,
            evidence_subject_ref="emerging-1",
        )
    )
    assert card.claim_type == "emerging_position"
    assert {ref.evidence_id for ref in card.evidence_refs} == {
        "jd-1",
        "jd-2",
        "jd-3",
    }
    assert card.used_evidence_ids == ("jd-1", "jd-2", "jd-3")
    assert card.effective_sample_size == 3
    assert card.raw_evidence_count == 3
    assert card.algorithm_version == "emerging-position.v1"
    assert card.evidence_algorithm_version == "evidence-independence.v2"
    assert card.evidence_config_hash == summary.config_hash
    assert card.coverage_status == "covered"
    assert card.uncertainty_state == "ok"
    assert card.authority_state == "candidate"
    assert card.next_action == "review"
    assert "emerging_definition_not_reviewed" in card.limitations
    assert "evidence_source_version_missing" in card.limitations


def test_emerging_published_card_is_authoritative_with_release_refs() -> None:
    record = EmergingRecord(
        candidate=_candidate(status="published"),
        created_at=None,
        updated_at=None,
    )
    card = assemble_insight_card(
        emerging_card_source(
            record,
            summary=_summary(),
            evidence_subject_ref="emerging-1",
        )
    )
    assert card.authority_state == "authoritative"
    assert card.release_refs == ("release-1",)
    assert card.data_refs == ("release-1",)
    assert card.next_action == "user_action"


def test_emerging_without_summary_stays_candidate_and_blocked() -> None:
    record = EmergingRecord(
        candidate=_candidate(status="approved"),
        created_at=None,
        updated_at=None,
    )
    card = assemble_insight_card(emerging_card_source(record))
    assert card.uncertainty_state == "blocked"
    assert card.authority_state == "candidate"
    assert card.next_action == "rerun"
    assert "evidence_summary_missing" in card.limitations


def test_emerging_qualified_false_adds_gate_limitation() -> None:
    record = EmergingRecord(
        candidate=_candidate(status="pending_review"),
        created_at=None,
        updated_at=None,
    )
    card = assemble_insight_card(
        emerging_card_source(
            record,
            summary=_summary(),
            evidence_subject_ref="emerging-1",
            qualified=False,
        )
    )
    assert "germination_assessment_not_qualified" in card.limitations


def _trend_record(
    status: str = "published",
    *,
    source_coverage: float = 0.8,
    evidence_refs: tuple[str, ...] = ("ev-1", "ev-2"),
    graph_version_id: str | None = "gv-9",
    skill_catalog_version: str | None = "cat-3",
) -> TrendReportRecord:
    skill = TrendSkill(
        skill_id="s1",
        skill_name="Skill",
        category="c",
        weight=1.0,
        confidence=0.9,
        importance_level="core",
        trend_score=0.8,
        evidence_count=1,
    )
    graph = TrendGraphSnapshot(
        position_id="pos-1",
        position_name="Position",
        graph_version="gv-9",
        skills=(skill,),
        relations=(),
        core_responsibilities=(),
        industry_scenarios=(),
        status="published",
    )
    return TrendReportRecord(
        report_id="report-1",
        position_id="pos-1",
        graph_version_id=graph_version_id,
        time_window_start=date(2026, 1, 1),
        time_window_end=date(2026, 3, 31),
        current_graph=graph,
        skill_weight_distribution=SkillWeightDistribution(
            core=(), high=(), bonus=(), edge=()
        ),
        new_skills=(),
        rising_skills=(),
        declining_skills=(),
        replaced_skills=(),
        skill_combo_shifts=(),
        risks=(),
        summary="summary",
        status=status,
        created_at=None,
        updated_at=None,
        provider_run_id="run-1",
        algorithm_version="trend.v2",
        formula_version="formula.v1",
        skill_catalog_version=skill_catalog_version,
        source_coverage=source_coverage,
        missing_sources=(),
        quality_flags=(),
        evidence_references=evidence_refs,
    )


def test_trend_card_maps_coverage_versions_and_evidence() -> None:
    card = assemble_insight_card(
        trend_report_card_source(_trend_record())
    )
    assert card.claim_type == "trend_change"
    assert card.authority_state == "authoritative"
    assert card.uncertainty_state == "ok"
    assert card.used_evidence_ids == ("ev-1", "ev-2")
    assert card.graph_version_refs == ("gv-9",)
    assert card.catalog_refs == ("cat-3",)
    assert card.data_refs == ("time_window:2026-01-01:2026-03-31",)
    assert card.algorithm_version == "trend.v2"
    assert card.evidence_algorithm_version == ""
    assert card.source_coverage == 0.8
    assert card.coverage_status == "unknown"
    assert any(
        item.startswith("source_coverage:") for item in card.coverage_summary
    )
    assert card.next_action == "user_action"


def test_trend_card_low_source_coverage_blocks_authoritative() -> None:
    card = assemble_insight_card(
        trend_report_card_source(
            _trend_record(source_coverage=0.4)
        )
    )
    assert card.uncertainty_state == "insufficient_evidence"
    assert card.authority_state == "candidate"
    assert card.next_action == "collect_evidence"
    assert card.coverage_status == "unknown"
    assert "source_coverage:0.4000" in card.coverage_summary
    assert "source_coverage_below_minimum" in card.limitations


def test_trend_card_without_evidence_is_blocked() -> None:
    card = assemble_insight_card(
        trend_report_card_source(
            _trend_record(evidence_refs=())
        )
    )
    assert card.uncertainty_state == "blocked"
    assert card.authority_state == "candidate"
    assert card.next_action == "rerun"
    assert "trend_report_without_evidence_refs" in card.limitations


def test_trend_card_exposes_ablation_sensitivity() -> None:
    records = [
        _record("e-1"),
        _record("e-2"),
        _record("e-3"),
        _record("e-4", published_at=date(2026, 8, 1)),
        _record("e-5", published_at=date(2026, 8, 2)),
        _record("e-6", published_at=date(2026, 9, 1)),
    ]
    request = IndependenceRequest(
        subject_ref="pos-1",
        release_id="release-1",
        coverage_status="covered",
    )
    certificate = build_certificate(
        records,
        request,
        conclusion=EvidenceSupportScoreConclusion(),
    )
    card = assemble_insight_card(
        trend_report_card_source(
            _trend_record(),
            certificate=certificate,
            evidence_subject_ref="pos-1",
        )
    )
    assert len(card.sensitivity_results) == 4
    assert "sensitivity_pending_verification" not in card.limitations


def test_trend_coverage_is_composed_with_summary_state() -> None:
    summary = build_summary(
        [
            _record("ev-1"),
            _record("ev-2"),
        ],
        IndependenceRequest(
            subject_ref="pos-1",
            release_id="release-1",
            coverage_status="covered",
        ),
    )
    card = assemble_insight_card(
        trend_report_card_source(
            _trend_record(source_coverage=0.4),
            summary=summary,
            evidence_subject_ref="pos-1",
        )
    )
    assert card.uncertainty_state == "insufficient_evidence"
    assert "source_coverage_below_minimum" in card.uncertainty_reasons
    assert card.coverage_status == "covered"
    assert card.evidence_algorithm_version == summary.algorithm_version


def _evolution_event(**overrides) -> dict:
    values = {
        "event_id": "evt-1-2-skill_emergence-001",
        "event_type": "skill_emergence",
        "position_id": "pos-1",
        "from_version": 1,
        "to_version": 2,
        "source_entities": [],
        "target_entities": [
            {"canonical_name": "Python", "skill_id": "s1"}
        ],
        "confidence": 0.9,
        "magnitude": 0.4,
        "evidence": {
            "lineage": {"release_id": "release-9"},
            "source_relations": [],
            "target_relations": [],
            "source": "graph_version_snapshot_diff",
            "evidence_refs": ["jd-1", "jd-2"],
        },
        "reason": "weight increased",
        "detector_version": "position-evolution-events.v1",
        "config_version": "config.v1",
        "catalog_snapshot_id": "cat-7",
    }
    values.update(overrides)
    return values


def test_evolution_event_card_maps_versions_algorithm_and_catalog() -> None:
    card = assemble_insight_card(
        evolution_event_card_source(_evolution_event())
    )
    assert card.claim_type == "role_migration"
    assert card.subject_ref == "pos-1"
    assert card.graph_version_refs == ("1", "2")
    assert card.catalog_refs == ("cat-7",)
    assert card.data_refs == ("release-9",)
    assert card.algorithm_version == "position-evolution-events.v1"
    assert card.used_evidence_ids == ("jd-1", "jd-2")
    assert card.uncertainty_state == "ok"
    assert card.authority_state == "candidate"


def test_evolution_event_without_explicit_evidence_does_not_invent_refs() -> None:
    card = assemble_insight_card(
        evolution_event_card_source(
            _evolution_event(
                evidence={
                    "lineage": {"release_id": "release-9"},
                    "source": "graph_version_snapshot_diff",
                }
            )
        )
    )
    assert card.evidence_refs == ()
    assert card.used_evidence_ids == ()
    assert "evolution_event_without_evidence_refs" in card.limitations


def test_evolution_event_with_summary_and_certificate() -> None:
    summary = build_summary(
        [
            _record("jd-1"),
            _record("jd-2"),
            _record("jd-3"),
        ],
        IndependenceRequest(
            subject_ref="pos-1",
            release_id="release-1",
            coverage_status="covered",
        ),
    )
    certificate = build_certificate(
        [
            _record("jd-1"),
            _record("jd-2"),
            _record("jd-3", published_at=date(2026, 8, 1)),
            _record("jd-4", published_at=date(2026, 8, 2)),
            _record("jd-5", published_at=date(2026, 9, 1)),
        ],
        IndependenceRequest(
            subject_ref="pos-1",
            release_id="release-1",
            coverage_status="covered",
        ),
        conclusion=EvidenceSupportScoreConclusion(),
    )
    card = assemble_insight_card(
        evolution_event_card_source(
            _evolution_event(
                evidence={
                    "lineage": {"release_id": "release-1"},
                    "source": "graph_version_snapshot_diff",
                    "evidence_refs": ["jd-1", "jd-2", "jd-3"],
                }
            ),
            summary=summary,
            certificate=certificate,
            evidence_subject_ref="pos-1",
        )
    )
    assert card.uncertainty_state == "ok"
    assert len(card.sensitivity_results) == 4
    assert "sensitivity_pending_verification" not in card.limitations


def _matching_result(**overrides) -> dict:
    values = {
        "scenario_id": "scenario-1",
        "baseline_evaluation_id": "eval-1",
        "baseline_score": 60.0,
        "scenario_score": 75.0,
        "score_delta": 15.0,
        "scenario_hard_gate_status": "passed",
        "generation_status": "completed",
        "target_reachable": True,
        "algorithm_version": "counterfactual-profile.v2",
        "scoring_config_version": "scoring-config.v1",
        "position_graph_version": "gv-4",
        "position_catalog_version": "cat-2",
        "cv_profile_version": "cv-2",
        "position_profile_version": "pp-1",
        "evidence_refs": [
            {
                "evidence_id": "ev-1",
                "source_object_type": "matching_evidence",
                "source_object_id": "ev-1",
                "source_document_id": "doc-1",
                "source_version": "v1",
            }
        ],
    }
    values.update(overrides)
    return values


def test_matching_what_if_card_uses_user_action_and_versions() -> None:
    card = assemble_insight_card(
        matching_what_if_card_source(_matching_result())
    )
    assert card.claim_type == "matching_what_if"
    assert card.subject_ref == "eval-1"
    assert card.uncertainty_state == "ok"
    assert card.authority_state == "candidate"
    assert card.next_action == "user_action"
    assert card.used_evidence_ids == ("ev-1",)
    assert card.graph_version_refs == ("gv-4",)
    assert card.catalog_refs == ("cat-2",)
    assert card.data_refs == ("cv-2", "pp-1")
    assert "scenario-1" in card.claim


def test_matching_what_if_hard_gate_blocked_forces_rerun() -> None:
    card = assemble_insight_card(
        matching_what_if_card_source(
            _matching_result(scenario_hard_gate_status="failed")
        )
    )
    assert card.uncertainty_state == "blocked"
    assert card.next_action == "rerun"
    assert card.authority_state == "candidate"


def test_matching_what_if_without_evidence_is_honest_blocked() -> None:
    card = assemble_insight_card(
        matching_what_if_card_source(
            _matching_result(evidence_refs=[])
        )
    )
    assert card.uncertainty_state == "blocked"
    assert "what_if_without_evidence_refs" in card.limitations
    assert card.next_action == "rerun"


def test_matching_what_if_human_decision_keeps_algorithm_output() -> None:
    from app.contexts.insight_cards import HumanDecision

    source = matching_what_if_card_source(
        _matching_result(),
        human_decision=HumanDecision(
            decision_id="decision-1",
            decision="approved",
            reason="accepted action set",
            original_authority_state="candidate",
        ),
    )
    card = assemble_insight_card(source)
    assert card.authority_state == "reviewed"
    assert card.next_action == "user_action"
    assert card.human_decision is not None
    assert card.human_decision.original_authority_state == "candidate"
    assert card.algorithm_version == "counterfactual-profile.v2"


def test_matching_claim_and_evidence_algorithm_identity_are_separate() -> None:
    summary = build_summary(
        [
            _record("ev-1", subject_ref="eval-1"),
        ],
        IndependenceRequest(
            subject_ref="eval-1",
            release_id="release-1",
            coverage_status="covered",
        ),
    )
    card = assemble_insight_card(
        matching_what_if_card_source(
            _matching_result(),
            summary=summary,
            evidence_subject_ref="eval-1",
        )
    )
    assert card.algorithm_version == "counterfactual-profile.v2"
    assert card.evidence_algorithm_version == "evidence-independence.v2"
    assert card.evidence_config_hash == summary.config_hash
    assert card.algorithm_config_hash is None
    assert card.algorithm_config_version == "scoring-config.v1"


def test_evolution_missing_detector_version_fallback() -> None:
    card = assemble_insight_card(
        evolution_event_card_source(
            _evolution_event(
                detector_version=None,
                config_version="config.v1",
            )
        )
    )
    assert card.algorithm_version == "config.v1"
    assert card.algorithm_config_version == "config.v1"


def test_matching_missing_algorithm_version_fallback() -> None:
    card = assemble_insight_card(
        matching_what_if_card_source(
            _matching_result(
                algorithm_version=None,
                scoring_algorithm_version="scoring.v1",
                scoring_config_version="scoring-config.v1",
            )
        )
    )
    assert card.algorithm_version == "scoring.v1"
    assert card.algorithm_config_version == "scoring-config.v1"


def test_trend_rejects_evidence_summary_for_other_position() -> None:
    summary = build_summary(
        [_record("e-1", subject_ref="other-position")],
        IndependenceRequest(
            subject_ref="other-position",
            release_id="release-1",
            coverage_status="covered",
        ),
    )
    with pytest.raises(ValueError, match="summary.subject_ref"):
        trend_report_card_source(
            _trend_record(),
            summary=summary,
            evidence_subject_ref="other-position",
        )


def test_evolution_rejects_evidence_summary_for_other_position() -> None:
    summary = build_summary(
        [_record("e-1", subject_ref="other-position")],
        IndependenceRequest(
            subject_ref="other-position",
            release_id="release-1",
            coverage_status="covered",
        ),
    )
    with pytest.raises(ValueError, match="summary.subject_ref"):
        evolution_event_card_source(
            _evolution_event(),
            summary=summary,
            evidence_subject_ref="other-position",
        )


def test_emerging_rejects_evidence_summary_for_other_candidate() -> None:
    summary = build_summary(
        [_record("e-1", subject_ref="other-candidate")],
        IndependenceRequest(
            subject_ref="other-candidate",
            release_id="release-1",
            coverage_status="covered",
        ),
    )
    record = EmergingRecord(candidate=_candidate(), created_at=None, updated_at=None)
    with pytest.raises(ValueError, match="summary.subject_ref"):
        emerging_card_source(
            record,
            summary=summary,
            evidence_subject_ref="other-candidate",
        )


def test_emerging_evidence_versions_bind_source_version() -> None:
    record = EmergingRecord(candidate=_candidate(), created_at=None, updated_at=None)
    card = assemble_insight_card(
        emerging_card_source(
            record,
            summary=_summary(),
            evidence_subject_ref="emerging-1",
            evidence_versions={
                "jd-1": "jd-v1",
                "jd-2": "jd-v2",
                "jd-3": "jd-v3",
            },
        )
    )
    assert all(ref.source_version.startswith("jd-v") for ref in card.evidence_refs)
    assert "evidence_source_version_missing" not in card.limitations


def test_human_decision_from_review_task_maps_approved() -> None:
    from app.contexts.insight_cards import human_decision_from_review_task

    decision = human_decision_from_review_task(
        {
            "task_id": "task-1",
            "status": "approved",
            "assignee_id": "reviewer-1",
            "review_comment": "accepted",
        },
        original_authority_state="candidate",
    )
    assert decision is not None
    assert decision.decision == "approved"
    assert decision.decision_id == "task-1"
    assert decision.decided_by == "reviewer-1"
    assert decision.reason == "accepted"
    assert decision.original_authority_state == "candidate"


def test_human_decision_from_review_task_ignores_pending() -> None:
    from app.contexts.insight_cards import human_decision_from_review_task

    assert (
        human_decision_from_review_task(
            {"task_id": "task-1", "status": "pending"}
        )
        is None
    )
