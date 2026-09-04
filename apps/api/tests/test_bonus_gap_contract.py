"""Bonus-scoring gap contract: the BFF must accept the new gap type."""

from __future__ import annotations

from app.schemas.matching_bff import GapAnalysisResponse, PrioritizedGapResponse


def _bonus_gap() -> dict:
    return {
        "gap_type": "bonus_skill_missing",
        "requirement_id": "bonus:skill_go",
        "skill_id": "skill_go",
        "priority": "medium",
        "priority_score": 54.0,
        "reason_codes": ["REQUIRED_SKILL_NOT_OBSERVED", "BONUS_SKILL_GAP"],
        "evidence": [],
        "position_evidence_present": True,
        "candidate_evidence_present": False,
        "source_match_type": "none",
        "transferable_skill_ids": [],
        "prerequisite_skill_ids": [],
        "transferability_score": 0.0,
        "current_ownership": None,
        "target_ownership": None,
        "score_effect_status": "modeled",
    }


def test_prioritized_gap_response_accepts_bonus_skill_missing():
    parsed = PrioritizedGapResponse.model_validate(_bonus_gap())
    assert parsed.gap_type == "bonus_skill_missing"


def test_gap_analysis_response_accepts_bonus_gap():
    gap = GapAnalysisResponse.model_validate(
        {
            "generation_status": "completed",
            "prioritized_gaps": [_bonus_gap()],
            "learning_path": [],
            "counterfactual_suggestions": [],
            "candidate_actions": [],
            "learning_routes": [],
            "skill_path_decisions": [],
            "algorithm_version": "deterministic-gap-path.v3",
            "config_version": "gap-analysis-config.v2",
            "gap_policy_version": "gap-priority.v2",
            "gap_policy_hash": "a" * 64,
        }
    )
    assert gap.prioritized_gaps[0].gap_type == "bonus_skill_missing"
