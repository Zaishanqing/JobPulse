"""Twenty deterministic Profile-Position acceptance pairs required by the task brief."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.application.evaluation import MatchEvaluationService
from app.application.what_if import WhatIfService


@pytest.mark.parametrize("case_index", range(20))
def test_twenty_profile_position_pairs_are_deterministic_and_preserve_baseline(
    case_index: int,
    cv_payload: dict,
    position_payload: dict,
) -> None:
    levels = ("basic", "working", "proficient", "advanced", "expert")
    current_level = levels[case_index % 3]
    target_level = levels[1 + case_index % 4]
    skill_index = case_index % 2
    skill_id = ("skill_python", "skill_sql")[skill_index]

    # Keep referential IDs intact; vary contract content to create twenty valid
    # Profile-Position pairs without invalidating nested evidence links.
    cv_payload["skills"][0]["normalization_confidence"] = 0.80 + case_index / 100
    position_payload["canonical_title"] = f"Acceptance position {case_index:02d}"
    position_payload["graph_version"] = f"graph-acceptance-{case_index:02d}"
    position_payload["required_skills"][skill_index]["required_level"] = target_level
    cv_payload["skills"][skill_index]["demonstrated_level"] = current_level
    if skill_index == 0:
        cv_payload["capability_profiles"][0]["demonstrated_level"] = current_level
        cv_payload["capability_evidence_links"][0]["demonstrated_level"] = current_level

    original_cv = deepcopy(cv_payload)
    evaluator = MatchEvaluationService()
    what_if = WhatIfService(evaluator)
    request = {
        "cv_profile": cv_payload,
        "position_profile": position_payload,
        "actions": [
            {
                "action_id": f"acceptance-action-{case_index:02d}",
                "action_type": "add_skill",
                "skill_id": skill_id,
                "target_level": target_level,
                "estimated_hours": 2 + case_index,
            }
        ],
    }

    direct_baseline = evaluator.evaluate(
        {"cv_profile": cv_payload, "position_profile": position_payload}
    )
    first = what_if.evaluate(request)
    second = what_if.evaluate(request)

    assert first.generation_status == "completed"
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.baseline_evaluation == direct_baseline
    assert cv_payload == original_cv
