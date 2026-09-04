from __future__ import annotations

from copy import deepcopy

import pytest

from app.application.deepseek_semantic_candidates import (
    DeepSeekSemanticCandidateConfig,
    DeepSeekSemanticCandidateService,
)
from app.application.evaluation import MatchEvaluationService
from app.domain.deepseek_candidates import (
    LLMSemanticCandidateBatch,
    RawDeepSeekSkillCandidate,
)
from app.domain.skill_relations import SkillRelation
from app.infrastructure.deepseek_semantic_source import DeepSeekSemanticCandidateSource
from app.ports.llm_semantic_candidates import (
    LLMSemanticCandidateError,
)


def _evidence(source: str, quote: str) -> dict:
    return {
        "source_id": source,
        "quote": quote,
        "start": 0,
        "end": len(quote),
        "alignment": "exact",
        "occurrence_index": 0,
    }


def _relation(relation_id: str, *, evidence: bool = True) -> SkillRelation:
    return SkillRelation(
        relation_id=relation_id,
        source_skill_id="skill_python",
        target_skill_id="skill_go",
        relation_type="related",
        source_system="knowledge-graph-test",
        graph_version="graph-test-v1",
        confidence=0.9,
        evidence_refs=(_evidence("graph:relation", "Python related to Go"),)
        if evidence
        else (),
    )


class FakeSource:
    def __init__(
        self,
        *,
        batch: LLMSemanticCandidateBatch | None = None,
        error: LLMSemanticCandidateError | None = None,
    ) -> None:
        self.batch = batch
        self.error = error
        self.calls = 0

    def generate_candidates(self, *, cv, position) -> LLMSemanticCandidateBatch:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.batch is None:
            return LLMSemanticCandidateBatch(
                model="deepseek-test",
                algorithm_version="deepseek-semantic-candidates.test.v1",
            )
        return self.batch


def _position_with_go(ready_position_json: dict) -> dict:
    position = deepcopy(ready_position_json)
    position["required_skills"].append(
        {
            "skill_id": "skill_go",
            "canonical_name": "Go",
            "required_level": "working",
            "importance": 0.8,
            "resolution_status": "resolved",
            "evidence_refs": [_evidence("jd:go", "Go required")],
        }
    )
    position["preferred_skills"] = []
    position["profile_version"] = "position-source.v1"
    return position


def _candidate(proposed: str = "related") -> RawDeepSeekSkillCandidate:
    return RawDeepSeekSkillCandidate(
        requirement_id="required:skill_go",
        required_skill_id="skill_go",
        required_skill_name="Go",
        candidate_skill_id="skill_python",
        candidate_skill_name="Python",
        proposed_relation_type=proposed,
        rationale="Python experience supports Go backend work",
        position_quote="Go required",
        candidate_quote="Python",
    )


def _service(
    source: FakeSource,
    *,
    mode: str,
    relations: tuple[SkillRelation, ...],
) -> MatchEvaluationService:
    return MatchEvaluationService(
        semantic_candidates=DeepSeekSemanticCandidateService(
            source,
            DeepSeekSemanticCandidateConfig(mode=mode),  # type: ignore[arg-type]
            relations=relations,
        ),
    )


def test_valid_deepseek_candidate_is_kg_evidence_validated_and_scored(
    ready_cv_json, ready_position_json
):
    source = FakeSource(
        batch=LLMSemanticCandidateBatch(
            model="deepseek-test",
            algorithm_version="deepseek-semantic-candidates.test.v1",
            candidates=(_candidate(),),
        )
    )
    service = _service(
        source,
        mode="enabled",
        relations=(_relation("relation_python_go"),),
    )

    result = service.evaluate(
        {
            "cv_profile": ready_cv_json,
            "position_profile": _position_with_go(ready_position_json),
        }
    )

    go = next(
        item for item in result.skill_results if item.requirement_id == "required:skill_go"
    )
    assert go.match_status == "partial"
    assert go.match_type == "related"
    assert go.related_candidate_skill_id == "skill_python"
    assert go.relation_evidence
    assert go.relation_graph_version == "graph-test-v1"
    assert go.candidate_evidence
    assert go.semantic_model == "deepseek-test"
    assert result.semantic_llm_status == "available"
    assert any(item.status == "valid" for item in result.semantic_llm_candidates)
    assert result.final_match_result is not None
    assert result.final_match_result.overall_score is not None


def test_unverifiable_deepseek_candidate_is_semantic_candidate_unknown_without_score(
    ready_cv_json, ready_position_json
):
    source = FakeSource(
        batch=LLMSemanticCandidateBatch(
            model="deepseek-test",
            algorithm_version="deepseek-semantic-candidates.test.v1",
            candidates=(_candidate("transferable"),),
        )
    )
    service = _service(
        source,
        mode="enabled",
        relations=(_relation("relation_no_evidence", evidence=False),),
    )

    result = service.evaluate(
        {
            "cv_profile": ready_cv_json,
            "position_profile": _position_with_go(ready_position_json),
        }
    )

    go = next(
        item for item in result.skill_results if item.requirement_id == "required:skill_go"
    )
    assert go.match_status == "unknown"
    assert go.match_type == "semantic_candidate"
    assert go.reason_code == "DEEPSEEK_KG_RELATION_MISSING"
    assert result.semantic_llm_status == "available"
    assert all(item.status == "unknown" for item in result.semantic_llm_candidates)
    contribution = next(
        item
        for item in result.final_match_result.score_contributions
        if item.result_id == "required:skill_go"
    )
    assert contribution.score_value is None


def test_shadow_mode_audits_without_changing_score_or_skill_relations(
    ready_cv_json, ready_position_json
):
    position = _position_with_go(ready_position_json)
    baseline = MatchEvaluationService().evaluate(
        {"cv_profile": ready_cv_json, "position_profile": position}
    )
    source = FakeSource(
        batch=LLMSemanticCandidateBatch(
            model="deepseek-test",
            algorithm_version="deepseek-semantic-candidates.test.v1",
            candidates=(_candidate(),),
        )
    )
    shadow = _service(
        source,
        mode="shadow",
        relations=(_relation("relation_python_go"),),
    ).evaluate({"cv_profile": ready_cv_json, "position_profile": position})

    assert shadow.semantic_llm_status == "available"
    assert shadow.semantic_llm_candidates
    assert shadow.skill_results == baseline.skill_results
    assert shadow.final_match_result.overall_score == baseline.final_match_result.overall_score
    assert (
        shadow.final_match_result.dimension_scores
        == baseline.final_match_result.dimension_scores
    )


@pytest.mark.parametrize("mode", ["enabled", "shadow"])
def test_deepseek_api_failure_is_explicit(mode, ready_cv_json, ready_position_json):
    source = FakeSource(
        error=LLMSemanticCandidateError(
            "DEEPSEEK_API_UNAVAILABLE", "offline for test"
        )
    )
    service = _service(source, mode=mode, relations=())

    result = service.evaluate(
        {
            "cv_profile": ready_cv_json,
            "position_profile": _position_with_go(ready_position_json),
        }
    )

    if mode == "enabled":
        assert result.evaluation_status == "rejected"
        assert result.error_code == "DEEPSEEK_CANDIDATE_UNAVAILABLE"
    else:
        assert result.evaluation_status == "completed"
        assert result.semantic_llm_status == "unavailable"
        assert result.semantic_llm_error_code == "DEEPSEEK_API_UNAVAILABLE"


def test_disabled_deepseek_mode_skips_source_and_keeps_deterministic_result(
    ready_cv_json, ready_position_json
):
    def forbidden(*args, **kwargs):
        raise AssertionError("DeepSeek source must not be called in disabled mode")

    source = FakeSource()
    source.generate_candidates = forbidden  # type: ignore[method-assign]
    service = _service(source, mode="disabled", relations=())

    result = service.evaluate(
        {
            "cv_profile": ready_cv_json,
            "position_profile": _position_with_go(ready_position_json),
        }
    )

    assert result.evaluation_status == "completed"
    assert result.semantic_llm_status == "disabled"
    go = next(
        item for item in result.skill_results if item.requirement_id == "required:skill_go"
    )
    assert go.match_status == "missing"
    assert source.calls == 0


def test_deepseek_source_is_llm_candidate_not_embedding(monkeypatch):
    calls = {}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    def fake_extract(self, system_prompt, user_prompt):
        calls["system"] = system_prompt
        calls["user"] = user_prompt
        return type(
            "Result",
            (),
            {
                "data": {
                    "candidates": [
                        {
                            "requirement_id": "required:skill_go",
                            "required_skill_id": "skill_go",
                            "required_skill_name": "Go",
                            "candidate_skill_id": "skill_python",
                            "candidate_skill_name": "Python",
                            "proposed_relation_type": "related",
                            "rationale": "evidence supported",
                            "position_quote": "Go required",
                            "candidate_quote": "Python",
                        }
                    ]
                },
                "raw_response": "{}",
            },
        )()

    monkeypatch.setattr(
        "app.infrastructure.deepseek_semantic_source.DeepSeekClient.extract",
        fake_extract,
    )
    source = DeepSeekSemanticCandidateSource(
        model="deepseek-test",
        algorithm_version="deepseek-semantic-candidates.test.v1",
    )

    batch = source.generate_candidates(
        cv=type(
            "CV",
            (),
            {
                "profile_version": "a" * 64,
                "capability_profiles": (),
                "capability_evidence_links": (),
            },
        )(),
        position=type("POS", (), {"required_skills": (), "preferred_skills": ()})(),
    )

    assert batch.candidates[0].candidate_skill_id == "skill_python"
    assert not hasattr(source, "embed")
    assert "技能语义候选召回器" in calls["system"]
