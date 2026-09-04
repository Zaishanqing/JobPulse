from __future__ import annotations

from copy import deepcopy

import httpx
import pytest

from app.application.evaluation import MatchEvaluationService
from app.domain.matching import MatchingAlgorithmConfig, build_match_evaluation
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.relation_matching import SkillRelationMatchingConfig
from app.infrastructure.relation_sources import (
    HttpSkillRelationSource,
    InMemorySkillRelationSource,
)


def _refresh(payload: dict) -> dict:
    payload["profile_version"] = "profile-source.v1"
    return payload


def _position_for(position_payload: dict, skill_id: str, level: str | None = "working"):
    payload = deepcopy(position_payload)
    payload["required_skills"] = [
        {
            "skill_id": skill_id,
            "canonical_name": skill_id,
            "required_level": level,
            "importance": 1.0,
            "resolution_status": "resolved",
            "evidence_refs": [
                {
                    "source_id": f"jd:relation:{skill_id}",
                    "quote": skill_id,
                    "start": 0,
                    "end": len(skill_id),
                    "alignment": "exact",
                    "occurrence_index": 0,
                }
            ],
        }
    ]
    payload["preferred_skills"] = []
    return _refresh(payload)


def _evaluate(cv_payload: dict, position_payload: dict, relations):
    service = MatchEvaluationService(
        relation_source=InMemorySkillRelationSource(relations)
    )
    return service.evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )


def _required(result):
    return next(
        item for item in result.skill_results if item.importance_level == "required"
    )


def test_equivalent_relation_matches_without_changing_exact_coverage(
    ready_cv_json, ready_position_json, skill_relations_fixture
):
    position = _position_for(ready_position_json, "skill_python_equivalent")

    evaluation = _evaluate(ready_cv_json, position, skill_relations_fixture)
    result = _required(evaluation)

    assert result.match_status == "matched"
    assert result.match_type == "equivalent"
    assert result.reason_code == "EQUIVALENT_SKILL_MATCH"
    assert result.related_candidate_skill_id == "skill_python"
    assert result.relation_type == "equivalent"
    assert result.relation_confidence == 0.95
    assert result.relation_evidence[0].source_id == "graph:eq:1"
    assert result.relation_source == "knowledge-graph-fixture"
    assert result.relation_graph_version == "graph-relations-v1"
    assert result.candidate_evidence
    assert result.transferability_score == 1.0
    assert evaluation.required_skill_coverage == 0.0
    assert evaluation.required_transferable_coverage == 1.0


@pytest.mark.parametrize(
    ("target", "relation_type", "reason", "weight"),
    [
        (
            "skill_service_transfer",
            "transferable",
            "TRANSFERABLE_SKILL_PARTIAL_MATCH",
            0.7,
        ),
    ],
)
def test_validated_transferable_relation_is_partial_with_configured_weight(
    ready_cv_json,
    ready_position_json,
    skill_relations_fixture,
    target,
    relation_type,
    reason,
    weight,
):
    position = _position_for(ready_position_json, target)

    evaluation = _evaluate(ready_cv_json, position, skill_relations_fixture)
    result = _required(evaluation)

    assert result.match_status == "partial"
    assert result.match_type == relation_type
    assert result.reason_code == reason
    assert result.transferability_score == weight
    assert evaluation.required_skill_coverage == 0.0
    assert evaluation.required_transferable_coverage == weight


@pytest.mark.parametrize(
    ("target", "relation_type", "reason"),
    [
        (
            "skill_programming_parent",
            "parent_child",
            "PARENT_CHILD_LEARNING_ONLY",
        ),
        (
            "skill_backend_related",
            "related",
            "RELATED_NO_SCORE_CREDIT",
        ),
    ],
)
def test_learning_only_relations_never_receive_score_credit(
    ready_cv_json,
    ready_position_json,
    skill_relations_fixture,
    target,
    relation_type,
    reason,
):
    position = _position_for(ready_position_json, target)

    evaluation = _evaluate(ready_cv_json, position, skill_relations_fixture)
    result = _required(evaluation)

    assert result.match_status == "missing"
    assert result.match_type == relation_type
    assert result.reason_code == reason
    assert result.transferability_score == 0.0
    assert evaluation.required_skill_coverage == 0.0
    assert evaluation.required_transferable_coverage == 0.0


def test_prerequisite_is_recorded_but_does_not_satisfy_target(
    ready_cv_json, ready_position_json, skill_relations_fixture
):
    position = _position_for(ready_position_json, "skill_advanced_target")

    evaluation = _evaluate(ready_cv_json, position, skill_relations_fixture)
    result = _required(evaluation)

    assert result.match_status == "missing"
    assert result.match_type == "prerequisite"
    assert result.reason_code == "PREREQUISITE_ONLY"
    assert result.transferability_score == 0.0
    assert evaluation.required_transferable_coverage == 0.0


def test_exact_match_is_never_overwritten_by_graph_relation(
    ready_cv_json, ready_position_json, skill_relations_fixture
):
    position = _position_for(ready_position_json, "skill_python")

    evaluation = _evaluate(ready_cv_json, position, skill_relations_fixture)
    result = _required(evaluation)

    assert result.match_status == "matched"
    assert result.match_type == "exact"
    assert result.reason_code == "EXACT_SKILL_LEVEL_MET"
    assert result.related_candidate_skill_id is None
    assert result.relation_type is None
    assert result.relation_evidence == ()
    assert evaluation.required_skill_coverage == 1.0
    assert evaluation.required_transferable_coverage == 1.0


@pytest.mark.parametrize("target", ["skill_low_confidence", "skill_no_evidence"])
def test_low_confidence_or_evidence_free_relation_is_ignored(
    ready_cv_json, ready_position_json, skill_relations_fixture, target
):
    position = _position_for(ready_position_json, target)

    result = _required(_evaluate(ready_cv_json, position, skill_relations_fixture))

    assert result.match_status == "missing"
    assert result.match_type == "none"
    assert result.reason_code == "RELATION_EVIDENCE_INSUFFICIENT"
    assert result.related_candidate_skill_id is None
    assert result.relation_evidence == ()


def test_duplicate_and_circular_relations_do_not_duplicate_score(
    ready_cv_json, ready_position_json, skill_relations_fixture
):
    duplicate_position = _position_for(
        ready_position_json, "skill_duplicate_target"
    )
    duplicate = _evaluate(
        ready_cv_json, duplicate_position, skill_relations_fixture
    )
    result = _required(duplicate)
    assert len(duplicate.skill_results) == 1
    assert result.match_status == "missing"
    assert result.transferability_score == 0.0
    assert result.relation_evidence[0].source_id == "graph:duplicate:b"

    cycle_position = _position_for(ready_position_json, "skill_cycle_target")
    forward = _evaluate(ready_cv_json, cycle_position, skill_relations_fixture)
    reverse = _evaluate(
        ready_cv_json, cycle_position, tuple(reversed(skill_relations_fixture))
    )
    assert len(forward.skill_results) == 1
    assert forward.required_transferable_coverage == 0.0
    assert forward == reverse


def test_relations_are_one_hop_and_do_not_infer_transitive_paths(
    ready_cv_json, ready_position_json, skill_relations_fixture
):
    position = _position_for(ready_position_json, "skill_transitive_target")

    result = _required(_evaluate(ready_cv_json, position, skill_relations_fixture))

    assert result.match_status == "missing"
    assert result.match_type == "none"
    assert result.reason_code == "REQUIRED_SKILL_NOT_OBSERVED"


def test_unknown_and_unresolved_exact_results_keep_their_meaning(
    ready_cv_json, ready_position_json, skill_relations_fixture
):
    position_payload = _position_for(ready_position_json, "skill_python")
    unknown_payload = deepcopy(ready_cv_json)
    unknown_payload["capability_profiles"][0]["demonstrated_level"] = "unknown"
    _refresh(unknown_payload)
    unknown = _required(
        _evaluate(unknown_payload, position_payload, skill_relations_fixture)
    )
    assert unknown.match_status == "unknown"
    assert unknown.match_type == "exact"
    assert unknown.relation_type is None

    unresolved_payload = deepcopy(ready_cv_json)
    unresolved_payload["capability_profiles"][0].update(
        {"resolution_status": "unresolved", "verification_status": "unresolved"}
    )
    _refresh(unresolved_payload)
    cv = CVMatchProfile.model_validate(unresolved_payload)
    position = PositionMatchProfile.model_validate(position_payload)
    unresolved = _required(
        build_match_evaluation(
            cv,
            position,
            MatchingAlgorithmConfig(),
            skill_relations_fixture,
        )
    )
    assert unresolved.match_status == "unresolved"
    assert unresolved.match_type == "exact"
    assert unresolved.relation_type is None


def test_bonus_transferable_coverage_is_separate(
    ready_cv_json, ready_position_json, skill_relations_fixture
):
    position = deepcopy(ready_position_json)
    position["preferred_skills"] = [
        {
            "skill_id": "skill_backend_related",
            "canonical_name": "Related backend",
            "required_level": "working",
            "importance": 0.5,
            "resolution_status": "resolved",
            "evidence_refs": [],
        }
    ]
    _refresh(position)

    evaluation = _evaluate(ready_cv_json, position, skill_relations_fixture)
    bonus = next(
        item for item in evaluation.skill_results if item.importance_level == "bonus"
    )

    assert bonus.match_status == "missing"
    assert evaluation.required_skill_coverage == 1.0
    assert evaluation.required_transferable_coverage == 1.0
    assert evaluation.bonus_skill_coverage == 0.0
    assert evaluation.bonus_transferable_coverage == 0.0


def test_relation_contract_keeps_source_version_confidence_and_evidence(
    skill_relations_fixture,
):
    relation = skill_relations_fixture[0]

    assert relation.source_system == "knowledge-graph-fixture"
    assert relation.graph_version == "graph-relations-v1"
    assert relation.confidence == 0.95
    assert relation.evidence_refs

    with pytest.raises(ValueError, match="between 0 and 1"):
        SkillRelationMatchingConfig(related_weight=1.1)


def test_http_relation_adapter_uses_explicit_contract(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "data": {
                    "graph_version": "graph.v1",
                    "relations": [
                        {
                            "relation_id": "relation-1",
                            "source_skill_id": "skill_a",
                            "target_skill_id": "skill_b",
                            "relation_type": "related",
                            "source_system": "knowledge-graph-service",
                            "graph_version": "graph.v1",
                            "confidence": 0.5,
                            "evidence_refs": [],
                        }
                    ],
                },
            }

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json, headers):
            assert url == "http://knowledge-graph/api/v1/relations/query"
            assert json == {
                "contract_version": "skill-relation-query.v1",
                "skill_ids": ["skill_a", "skill_b"],
            }
            assert headers == {"Accept": "application/json"}
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    adapter = HttpSkillRelationSource(
        "http://knowledge-graph/",
        "/api/v1/relations/query",
        timeout_seconds=2,
    )

    assert (
        adapter.fetch_relations(("skill_b", "skill_a", "skill_a"))[0].relation_id
        == "relation-1"
    )
