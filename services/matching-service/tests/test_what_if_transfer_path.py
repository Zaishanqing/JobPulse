from __future__ import annotations

from copy import deepcopy

import pytest

from app.application.evaluation import MatchEvaluationService
from app.application.what_if import WhatIfService
from app.domain.gaps import SkillPathEdge, SkillTransferPath
from app.domain.profiles import CVMatchProfile, Evidence, PositionMatchProfile
from app.domain.what_if import WhatIfAction, WhatIfActionSetError, apply_actions

GRAPH_VERSION = "graph-42"


def edge(
    relation_id: str,
    source_skill_id: str,
    target_skill_id: str,
    *,
    hop: int,
    confidence: float = 0.9,
    graph_version: str = GRAPH_VERSION,
) -> SkillPathEdge:
    return SkillPathEdge(
        relation_id=relation_id,
        source_skill_id=source_skill_id,
        target_skill_id=target_skill_id,
        relation_type="transferable",
        graph_version=graph_version,
        confidence=confidence,
        hop_number=hop,
        edge_cost_hours=5.0,
        score_credit_allowed=True,
        evidence_refs=(
            Evidence(
                source_id=f"graph:{relation_id}",
                quote="graph relation evidence",
                alignment="unresolved",
            ),
        ),
    )


def transfer_path(
    path_id: str,
    source_skill_id: str,
    target_skill_id: str,
    edges: tuple[SkillPathEdge, ...],
    *,
    graph_version: str = GRAPH_VERSION,
) -> SkillTransferPath:
    node_skill_ids = (edges[0].source_skill_id, *(item.target_skill_id for item in edges))
    confidences = tuple(item.confidence for item in edges)
    return SkillTransferPath(
        path_id=path_id,
        source_skill_id=source_skill_id,
        target_skill_id=target_skill_id,
        target_requirement_id="requirement-sql",
        node_skill_ids=node_skill_ids,
        edges=edges,
        hop_count=len(edges),
        total_cost_hours=round(sum(item.edge_cost_hours for item in edges), 4),
        minimum_confidence=min(confidences),
        effective_confidence=round(min(confidences) * (0.8 ** (len(edges) - 1)), 6),
        outcome_status="eligible" if len(edges) == 1 else "partial",
        graph_version_id=graph_version,
        score_credit_allowed=all(
            item.relation_type in {"equivalent", "transferable"}
            for item in edges
        ),
        suitable_for_learning=True,
    )


class FakeSkillTransferPathResolver:
    def __init__(self, *paths: SkillTransferPath) -> None:
        self._paths = {item.path_id: item for item in paths}

    def resolve_paths(
        self, path_refs: tuple[str, ...], *, graph_version: str
    ) -> tuple[SkillTransferPath, ...]:
        return tuple(
            item
            for ref in path_refs
            if (item := self._paths.get(ref)) is not None
        )


def transfer_action(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "action_id": "transfer-to-sql",
        "action_type": "controlled_skill_transfer",
        "skill_id": "skill_sql",
        "source_skill_id": "skill_python",
        "path_refs": ("path-one",),
        "graph_version": GRAPH_VERSION,
    }
    base.update(overrides)
    return base


def evaluate(
    cv_payload: dict,
    position_payload: dict,
    action: dict[str, object],
    *paths: SkillTransferPath,
) -> object:
    service = WhatIfService(
        MatchEvaluationService(),
        transfer_path_resolver=FakeSkillTransferPathResolver(*paths),
    )
    return service.evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [action],
        }
    )


def applied_capability(
    cv_payload: dict,
    position_payload: dict,
    action: WhatIfAction,
) -> object:
    cv = CVMatchProfile.model_validate(cv_payload)
    position = PositionMatchProfile.model_validate(position_payload)
    scenario = apply_actions(cv, position, (action,), scenario_id="scenario-test")
    return next(
        item for item in scenario.capability_profiles if item.skill_id == action.skill_id
    )


def test_valid_one_hop_transfer_uses_path_confidence_and_provenance(
    cv_payload: dict, position_payload: dict
) -> None:
    path = transfer_path(
        "path-one",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1),),
    )

    result = evaluate(
        cv_payload,
        position_payload,
        transfer_action(),
        path,
    )

    assert result.generation_status == "completed"
    action = result.actions[0]
    assert action.validated_path_refs == ("path-one",)
    assert action.source_confidence == 0.8
    assert action.path_quality == 0.9
    assert action.edge_confidences == (0.9,)
    assert action.target_confidence == 0.72
    assert action.confidence_algorithm_version == "controlled-skill-transfer-confidence.v2"
    assert "controlled-skill-transfer-confidence.v2" in action.confidence_basis
    capability = applied_capability(cv_payload, position_payload, action)
    assert capability.support_confidence == 0.72
    cv = CVMatchProfile.model_validate(cv_payload)
    position = PositionMatchProfile.model_validate(position_payload)
    scenario = apply_actions(cv, position, (action,), scenario_id="scenario-test")
    link = next(
        item
        for item in scenario.capability_evidence_links
        if item.skill_id == action.skill_id
    )
    evidence = link.evidence_refs[0]
    assert evidence.source_id == "what-if:transfer-to-sql"
    assert "hypothetical" in evidence.quote
    assert "controlled-skill-transfer:skill_python->skill_sql" in evidence.quote
    assert "graph-42" in evidence.quote
    assert "path-one" in evidence.quote


def test_valid_multi_hop_transfer_applies_hop_discount(
    cv_payload: dict, position_payload: dict
) -> None:
    path = transfer_path(
        "path-multi",
        "skill_python",
        "skill_sql",
        (
            edge("edge-1", "skill_python", "skill_middle", hop=1, confidence=0.9),
            edge("edge-2", "skill_middle", "skill_sql", hop=2, confidence=0.8),
        ),
    )

    result = evaluate(
        cv_payload,
        position_payload,
        transfer_action(path_refs=("path-multi",)),
        path,
    )

    assert result.generation_status == "completed"
    action = result.actions[0]
    assert action.validated_path_refs == ("path-multi",)
    assert action.edge_confidences == (0.9, 0.8)
    assert action.path_quality == 0.64
    assert action.target_confidence == 0.512
    assert action.transfer_hop_count == 2
    assert action.transfer_outcome_status == "partial"
    capability = applied_capability(cv_payload, position_payload, action)
    assert capability.support_confidence == 0.512
    skill_result = next(
        item
        for item in result.scenario_evaluation.skill_results
        if item.skill_id == action.skill_id
    )
    assert skill_result.match_status == "partial"
    assert skill_result.match_type == "transferable"
    assert skill_result.reason_code == "CONTROLLED_TRANSFER_PARTIAL_MATCH"


def test_missing_source_skill_rejects(cv_payload: dict, position_payload: dict) -> None:
    path = transfer_path(
        "path-one",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1),),
    )
    action = transfer_action(source_skill_id="skill_missing")

    result = evaluate(cv_payload, position_payload, action, path)

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_TRANSFER_SOURCE_MISSING"


def test_not_observed_source_rejects(cv_payload: dict, position_payload: dict) -> None:
    path = transfer_path(
        "path-one",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1),),
    )
    cv_payload["capability_profiles"][0]["verification_status"] = "not_observed"

    result = evaluate(
        cv_payload,
        position_payload,
        transfer_action(),
        path,
    )

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_TRANSFER_SOURCE_NOT_OBSERVED"


def test_missing_path_ref_rejects(cv_payload: dict, position_payload: dict) -> None:
    path = transfer_path(
        "path-one",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1),),
    )
    action = transfer_action(path_refs=("path-missing",))

    result = evaluate(cv_payload, position_payload, action, path)

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_TRANSFER_PATH_UNRESOLVED"


def test_disconnected_path_rejects(cv_payload: dict, position_payload: dict) -> None:
    path = transfer_path(
        "path-broken",
        "skill_python",
        "skill_sql",
        (
            edge("edge-1", "skill_python", "skill_middle", hop=1),
            edge("edge-2", "skill_other", "skill_sql", hop=2),
        ),
    )

    result = evaluate(
        cv_payload,
        position_payload,
        transfer_action(path_refs=("path-broken",)),
        path,
    )

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_TRANSFER_PATH_DISCONNECTED"


def test_wrong_endpoint_rejects(cv_payload: dict, position_payload: dict) -> None:
    path = transfer_path(
        "path-endpoint",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_other", hop=1),),
    )

    result = evaluate(
        cv_payload,
        position_payload,
        transfer_action(path_refs=("path-endpoint",)),
        path,
    )

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_TRANSFER_ENDPOINT_MISMATCH"


def test_graph_version_mismatch_rejects(cv_payload: dict, position_payload: dict) -> None:
    path = transfer_path(
        "path-one",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1),),
    )
    action = transfer_action(graph_version="graph-old")

    result = evaluate(cv_payload, position_payload, action, path)

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_TRANSFER_GRAPH_VERSION_MISMATCH"


def test_path_edge_graph_version_mismatch_rejects(
    cv_payload: dict, position_payload: dict
) -> None:
    path = transfer_path(
        "path-one",
        "skill_python",
        "skill_sql",
        (
            edge(
                "edge-1",
                "skill_python",
                "skill_sql",
                hop=1,
                graph_version="graph-other",
            ),
        ),
        graph_version=GRAPH_VERSION,
    )

    result = evaluate(cv_payload, position_payload, transfer_action(), path)

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_TRANSFER_GRAPH_VERSION_MISMATCH"


def test_confidence_calculation_is_deterministic(
    cv_payload: dict, position_payload: dict
) -> None:
    path = transfer_path(
        "path-one",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1, confidence=0.9),),
    )

    first = evaluate(cv_payload, position_payload, transfer_action(), path)
    second = evaluate(cv_payload, position_payload, transfer_action(), path)

    assert first.generation_status == "completed"
    assert second.generation_status == "completed"
    assert first.scenario_id == second.scenario_id
    assert first.actions[0].target_confidence == second.actions[0].target_confidence


def test_lower_edge_confidence_lowers_target_confidence(
    cv_payload: dict, position_payload: dict
) -> None:
    high_path = transfer_path(
        "path-high",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1, confidence=0.9),),
    )
    low_path = transfer_path(
        "path-low",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1, confidence=0.7),),
    )

    high = evaluate(
        cv_payload, position_payload, transfer_action(path_refs=("path-high",)), high_path
    )
    low = evaluate(
        cv_payload, position_payload, transfer_action(path_refs=("path-low",)), low_path
    )

    assert high.generation_status == "completed"
    assert low.generation_status == "completed"
    assert high.actions[0].target_confidence == 0.72
    assert low.actions[0].target_confidence == 0.56
    assert low.actions[0].target_confidence < high.actions[0].target_confidence
    assert (
        applied_capability(cv_payload, position_payload, high.actions[0]).support_confidence
        == 0.72
    )
    assert (
        applied_capability(cv_payload, position_payload, low.actions[0]).support_confidence
        == 0.56
    )


def test_rejected_transfer_does_not_mutate_cv_profile(
    cv_payload: dict, position_payload: dict
) -> None:
    original = deepcopy(cv_payload)
    path = transfer_path(
        "path-one",
        "skill_python",
        "skill_sql",
        (edge("edge-1", "skill_python", "skill_sql", hop=1),),
    )
    action = transfer_action(path_refs=("path-missing",))

    result = evaluate(cv_payload, position_payload, action, path)

    assert result.generation_status == "rejected"
    assert cv_payload == original


def test_unannotated_transfer_cannot_fall_back_to_add_skill(
    cv_payload: dict, position_payload: dict
) -> None:
    cv = CVMatchProfile.model_validate(cv_payload)
    position = PositionMatchProfile.model_validate(position_payload)
    action = WhatIfAction.model_validate(transfer_action())

    with pytest.raises(WhatIfActionSetError) as exc_info:
        apply_actions(cv, position, (action,), scenario_id="scenario-test")

    assert exc_info.value.code == "WHAT_IF_TRANSFER_VALIDATION_REQUIRED"


def test_controlled_transfer_without_resolver_rejects(
    cv_payload: dict, position_payload: dict
) -> None:
    service = WhatIfService(MatchEvaluationService())

    result = service.evaluate(
        {
            "cv_profile": cv_payload,
            "position_profile": position_payload,
            "actions": [transfer_action()],
        }
    )

    assert result.generation_status == "rejected"
    assert result.error_code == "WHAT_IF_TRANSFER_RESOLVER_UNAVAILABLE"
