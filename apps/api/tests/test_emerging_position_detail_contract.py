from __future__ import annotations

from app.api.emerging_position_mapping import assessment_data
from app.contexts.emerging_positions import (
    ClusterRecord,
    EmergingActor,
    EmergingRecord,
    GerminationAssessmentRecord,
    QueryGerminationAssessment,
    ReleaseGateConfig,
)
from app.contexts.emerging_positions.application import _gate
from app.domain.emerging_position import (
    EmergingCandidate,
    GerminationAssessment,
    ReleaseGateEvidence,
)
from app.domain.values import freeze


def _cluster() -> ClusterRecord:
    return ClusterRecord(
        cluster_id="cluster-1",
        cluster_name="Agentic RAG",
        core_skills=(freeze({"raw_skill": "RAG"}),),
        representative_jd_ids=("jd-1", "jd-2", "jd-3", "jd-4"),
        stability_score=1.0,
        discovery_run_id="run-1",
        discovery_run_status="succeeded",
        assessment=GerminationAssessment.from_values(
            {
                "germination_score": 0.78,
                "qualified_as_emerging": False,
                "level": "watchlist",
                "decision_reason": "latest cluster does not span enough windows",
                "score_dimensions": {"cluster_growth_rate": 0.88},
                "evidence_package": {
                    "algorithm_version": "discovery-v4",
                    "formula_version": "emergence-index-v4-seven-dimensions",
                    "score_components": [
                        {
                            "name": "growth",
                            "normalized_value": 0.88,
                            "weight": 0.18,
                            "contribution": 0.1584,
                        }
                    ],
                    "diagnostic_features": {"legacy": {"scored": False}},
                    "emergence_index": {
                        "dimensions": {
                            "growth": {"normalized_value": 0.88},
                            "cross_window_persistence": {"normalized_value": 0.14},
                            "enterprise_coverage": {"normalized_value": 1.0},
                            "source_diversity": {"normalized_value": 1.0},
                            "standard_position_distance": {"normalized_value": 0.83},
                            "evidence_quality": {"normalized_value": 0.75},
                            "result_stability": {"normalized_value": 1.0},
                        }
                    },
                },
            },
            "run-1",
        ),
        generated_definition=freeze({"growth_trajectory": []}),
    )


def _candidate() -> EmergingCandidate:
    return EmergingCandidate.create(
        candidate_id="emerging-1",
        cluster_id="cluster-1",
        position_name="Agentic RAG 应用工程师",
        core_responsibilities=["交付 Agentic RAG 应用"],
        required_skills=[{"raw_skill": "RAG"}],
        bonus_skills=[],
        industry_scenarios=["企业知识库"],
        germination_score=0.78,
        score_dimensions={"cluster_growth_rate": 0.88},
        evidence_jd_ids=["jd-1", "jd-2", "jd-3", "jd-4"],
        status="published",
        field_evidence={
            "candidate_lifecycle": {
                "status": "stable_emerging_role",
                "emergence_score": 0.81,
                "observed_window_ids": ["w1", "w2", "w3", "w4", "w5"],
            }
        },
    )


class _Repository:
    def __init__(self, cluster: ClusterRecord, candidate: EmergingCandidate) -> None:
        self.cluster = cluster
        self.record = EmergingRecord(candidate, None, None)

    def get(self, emerging_id: str) -> EmergingRecord | None:
        return self.record if emerging_id == self.record.candidate.candidate_id else None

    def get_cluster(self, cluster_id: str) -> ClusterRecord | None:
        return self.cluster if cluster_id == self.cluster.cluster_id else None

    @staticmethod
    def release_config() -> ReleaseGateConfig:
        return ReleaseGateConfig(0.65, 0.6)


class _UnitOfWork:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def test_detail_query_uses_the_same_lifecycle_assessment_as_release_gate():
    cluster = _cluster()
    candidate = _candidate()
    repository = _Repository(cluster, candidate)
    config = repository.release_config()

    queried = QueryGerminationAssessment(lambda: _UnitOfWork(repository)).execute(
        candidate.candidate_id,
        EmergingActor("account-1", "personal_user"),
    )
    gated = _gate(cluster, candidate, config)

    assert cluster.assessment.qualified is False
    assert queried.assessment == gated.assessment
    assert queried.assessment.qualified is True
    assert queried.assessment.level == "stable_emerging_role"
    assert queried.assessment.score == 0.81
    assert queried.qualification_basis == "candidate_lifecycle"
    assert "5 JD publish-date windows" in queried.assessment.decision_reason


def test_assessment_mapping_exposes_the_canonical_and_compatibility_contracts():
    record = GerminationAssessmentRecord(
        "emerging-1",
        _cluster().assessment,
        "run-1",
        "cluster_assessment",
    )

    data = assessment_data(record)

    assert data["score_components"][0]["name"] == "growth"
    assert data["diagnostic_features"] == {"legacy": {"scored": False}}
    assert data["evidence_package"] == data["evidence_summary"]
    assert data["algorithm_version"] == "discovery-v4"
    assert data["qualification_basis"] == "cluster_assessment"
    assert data["score_dimensions_status"] == "legacy_diagnostic_not_scored"


def test_formal_experiment_projection_uses_its_frozen_release_contract():
    assessment = GerminationAssessment.from_values(
        {
            "state": "emerging",
            "source": "formal_experiment_import",
            "experiment_id": "EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823",
            "reason": "正式实验通过",
            "counts": {"distinct_dates": 2, "independent_postings": 2},
            "gates": {
                "structural_signal": True,
                "independent_posting_persistence": True,
                "diffusion": True,
                "temporal_persistence_growth_or_evolution": True,
                "any_temporal_evidence": True,
            },
        },
        "formal-exp-emerge-01-v3.2-20260823",
    )

    assert assessment.qualified is True
    assert assessment.score == 1.0
    assert assessment.evidence_package["formal_experiment"]["accepted"] is True
    gate = ReleaseGateEvidence(
        run_succeeded=True,
        stability_score=1.0,
        minimum_stability_score=0.65,
        assessment=assessment,
        emerging_threshold=0.6,
        evidence_jd_ids=("jd-1", "jd-2"),
        real_member_count=2,
        window_count=2,
        complete_score_dimensions=False,
        complete_definition=True,
        complete_claim_evidence=True,
        definition_unchanged_since_approval=True,
        formal_experiment_accepted=True,
    )
    assert gate.failures() == ()
