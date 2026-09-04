from __future__ import annotations

from copy import deepcopy

from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.learning_paths import LearningPathService
from app.domain.matching import (
    MatchingAlgorithmConfig,
    prepare_effective_position,
)
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.requirement_graph import (
    SPECIALTY_ROUTE_GRAPH_VERSION,
    select_specialty_route,
)
from app.domain.tasks import POSITION_REQUIREMENT_GRAPH_NOT_APPLICABLE
from app.infrastructure.memory_repositories import InMemoryPersistence

ROUTE_A = ("req-cuda", "req-vllm", "req-sglang")
ROUTE_B = ("req-megatron", "req-rlhf", "req-pytorch")


def _evidence(source_id: str, quote: str) -> dict[str, object]:
    return {
        "source_id": source_id,
        "quote": quote,
        "start": 0,
        "end": len(quote),
        "alignment": "exact",
        "occurrence_index": 0,
    }


def _route_graph() -> dict[str, object]:
    evidence = _evidence("jd:route", "source JD required skills")
    return {
        "graph_version": SPECIALTY_ROUTE_GRAPH_VERSION,
        "status": "complete",
        "groups": [
            {
                "requirement_group_id": "standard-route:route-a",
                "group_type": "and",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": item}
                    for item in ROUTE_A
                ],
                "evidence": evidence,
                "confidence": 1.0,
            },
            {
                "requirement_group_id": "standard-route:route-b",
                "group_type": "and",
                "priority": "required",
                "children": [
                    {"node_type": "requirement_ref", "ref_id": item}
                    for item in ROUTE_B
                ],
                "evidence": evidence,
                "confidence": 1.0,
            },
            {
                "requirement_group_id": "standard-route-root:test",
                "group_type": "one_of",
                "priority": "required",
                "children": [
                    {"node_type": "group_ref", "ref_id": "standard-route:route-a"},
                    {"node_type": "group_ref", "ref_id": "standard-route:route-b"},
                ],
                "evidence": evidence,
                "confidence": 1.0,
            },
        ],
        "unresolved_items": [],
    }


def _position_payload(ready_position_json: dict) -> dict:
    payload = deepcopy(ready_position_json)
    names = {
        "req-cuda": ("CUDA", "A"),
        "req-vllm": ("vLLM", "A"),
        "req-sglang": ("SGLang", "A"),
        "req-megatron": ("Megatron-LM", "B"),
        "req-rlhf": ("RLHF", "B"),
        "req-pytorch": ("PyTorch", "B"),
    }
    payload.update(
        {
            "position_id": "position_ai_inference_fixture",
            "canonical_position_id": "position_ai_inference",
            "canonical_title": "AI Inference Optimization Engineer",
            "core_responsibilities": [],
            "hard_conditions": [],
            "preferred_skills": [],
            "requirement_graph": _route_graph(),
            "required_skills": [
                {
                    "requirement_id": requirement_id,
                    "skill_id": f"skill-{requirement_id.removeprefix('req-')}",
                    "canonical_name": name,
                    "required_level": "working",
                    "importance": 1.0,
                    "resolution_status": "resolved",
                    "evidence_refs": [
                        _evidence(f"jd:{branch}:{requirement_id}", name)
                    ],
                }
                for requirement_id, (name, branch) in names.items()
            ],
        }
    )
    return payload


def _cv_payload(
    ready_cv_json: dict,
    skill_specs: tuple[tuple[str, str], ...] = (
        ("req-megatron", "Megatron-LM"),
        ("req-rlhf", "RLHF"),
        ("req-pytorch", "PyTorch"),
    ),
) -> dict:
    payload = deepcopy(ready_cv_json)
    skills = []
    capabilities = []
    links = []
    for index, (requirement_id, name) in enumerate(skill_specs, 1):
        skill_id = f"skill-{requirement_id.removeprefix('req-')}"
        link_id = f"link-{skill_id}"
        ref = _evidence(f"cv:skill:{index}", name)
        skills.append(
            {
                "aggregation_key": f"skill:{name.casefold()}",
                "skill_id": skill_id,
                "canonical_name": name,
                "normalization_confidence": 1.0,
                "resolution_source": "canonical_name",
                "declared_level": "proficient",
                "demonstrated_level": "proficient",
                "verification_status": "supported",
                "resolution_status": "resolved",
                "evidence_refs": [ref],
            }
        )
        capabilities.append(
            {
                "profile_id": f"cap-{skill_id}",
                "document_id": payload["cv_id"],
                "aggregation_key": f"skill:{name.casefold()}",
                "skill_id": skill_id,
                "canonical_name": name,
                "declared_feature_ids": [],
                "experience_skill_feature_ids": [],
                "evidence_link_ids": [link_id],
                "declared_level": "proficient",
                "demonstrated_level": "proficient",
                "demonstrated_level_label": "proficient",
                "verification_status": "supported",
                "support_confidence": 0.95,
                "confidence_band": "high",
                "independent_experience_count": 1,
                "aggregate_support_score": 4,
                "evidence_bonus": 0.2,
                "resolution_status": "resolved",
            }
        )
        links.append(
            {
                "link_id": link_id,
                "document_id": payload["cv_id"],
                "aggregation_key": f"skill:{name.casefold()}",
                "skill_id": skill_id,
                "canonical_name": name,
                "declared_feature_ids": [],
                "experience_skill_feature_id": f"experience_{skill_id}",
                "experience_feature_id": "work_fixture_001",
                "supporting_task_feature_ids": [],
                "support_signals": ["direct_experience"],
                "support_score": 4,
                "demonstrated_level": "proficient",
                "support_confidence": 0.95,
                "confidence_band": "high",
                "evidence_refs": [ref],
                "taxonomy_version": "taxonomy-fixture-v1",
                "derivation_version": "capability-verification.v1",
            }
        )
    payload["skills"] = skills
    payload["capability_profiles"] = capabilities
    payload["capability_evidence_links"] = links
    payload["work_experiences"][0]["tool_skill_ids"] = [
        f"skill-{requirement_id.removeprefix('req-')}"
        for requirement_id, _name in skill_specs
    ]
    project = deepcopy(payload["work_experiences"][0])
    project["experience_id"] = "project_fixture_001"
    project["kind"] = "project"
    payload["projects"] = [project]
    payload["evidence_refs"] = []
    return payload


def _models(
    ready_cv_json: dict,
    ready_position_json: dict,
    skill_specs: tuple[tuple[str, str], ...] = (
        ("req-megatron", "Megatron-LM"),
        ("req-rlhf", "RLHF"),
        ("req-pytorch", "PyTorch"),
    ),
):
    return (
        CVMatchProfile.model_validate(_cv_payload(ready_cv_json, skill_specs)),
        PositionMatchProfile.model_validate(_position_payload(ready_position_json)),
    )


def test_standard_position_selects_one_route_and_uses_it_everywhere(
    ready_cv_json, ready_position_json
):
    cv, position = _models(ready_cv_json, ready_position_json)
    config = MatchingAlgorithmConfig()

    evaluation = MatchEvaluationService().evaluate(
        {
            "cv_profile": cv.model_dump(mode="python"),
            "position_profile": position.model_dump(mode="python"),
            "target_type": "standard_position",
        },
        include_semantic=False,
    )

    assert evaluation.evaluation_status == "completed"
    required = {
        item.requirement_id
        for item in evaluation.skill_results
        if item.importance_level == "required"
    }
    assert required == set(ROUTE_B)
    assert {
        item.requirement_id
        for item in evaluation.skill_results
        if item.importance_level == "bonus"
    } >= set(ROUTE_A)
    assert evaluation.required_skill_coverage == 1.0
    assert evaluation.required_transferable_coverage == 1.0
    assert evaluation.information_sufficient is True
    assert "REQUIRED_SKILL_UNCERTAIN" not in evaluation.information_sufficiency_reasons
    assert evaluation.summary is not None
    assert evaluation.summary.required_skill_matched_count == 3
    assert evaluation.summary.required_skill_missing_count == 0
    assert evaluation.project_results
    assert evaluation.project_results[0].required_skill_ids == tuple(
        sorted(
            f"skill-{requirement_id.removeprefix('req-')}" for requirement_id in ROUTE_B
        )
    )
    assert not set(ROUTE_A).intersection(
        evaluation.project_results[0].required_skill_ids
    )
    groups = {
        item.group_id: item for item in evaluation.requirement_group_results
    }
    root = groups["standard-route-root:test"]
    assert root.status == "satisfied"
    assert set(root.covered_result_ids) == set(ROUTE_B)
    assert evaluation.final_match_result is not None
    assert evaluation.final_match_result.overall_score > 70
    assert {
        item.result_id
        for item in evaluation.final_match_result.score_contributions
        if item.dimension in {"required_skills", "capability_level", "projects"}
    }.isdisjoint(ROUTE_A)
    capability_dimension = next(
        item
        for item in evaluation.final_match_result.dimension_scores
        if item.dimension == "capability_level"
    )
    assert capability_dimension.applicable_count == 1
    assert capability_dimension.score == 100.0
    assert all(
        requirement_id not in " ".join(item.message for item in evaluation.final_match_result.gaps)
        for requirement_id in ROUTE_A
    )

    effective, selection = prepare_effective_position(
        cv,
        position,
        config,
        target_type="standard_position",
    )
    assert selection is not None
    assert selection.required_requirement_ids == ROUTE_B
    assert tuple(item.requirement_id for item in effective.required_skills) == ROUTE_B


def test_standard_position_route_selection_is_deterministic_and_dedupes_graph_refs(
    ready_cv_json, ready_position_json
):
    cv, position = _models(ready_cv_json, ready_position_json)
    first = prepare_effective_position(
        cv, position, MatchingAlgorithmConfig(), target_type="standard_position"
    )
    second = prepare_effective_position(
        cv, position, MatchingAlgorithmConfig(), target_type="standard_position"
    )
    assert first[1] == second[1]
    assert first[1] is not None
    assert first[1].route_id == "standard-route:route-b"

    duplicate_route = position.requirement_graph.model_copy(
        update={
            "groups": tuple(
                group.model_copy(
                    update={
                        "children": (
                            *group.children,
                            group.children[0],
                        )
                    }
                )
                if group.requirement_group_id == "standard-route:route-b"
                else group
                for group in position.requirement_graph.groups
            )
        }
    )
    duplicate_position = position.model_copy(update={"requirement_graph": duplicate_route})
    preliminary = tuple(
        item
        for item in MatchEvaluationService().evaluate(
            {
                "cv_profile": cv.model_dump(mode="python"),
                "position_profile": position.model_dump(mode="python"),
            },
            include_semantic=False,
        ).skill_results
    )
    selection = select_specialty_route(duplicate_position, preliminary)
    assert selection is not None
    assert selection.required_requirement_ids == ROUTE_B
    duplicate_evaluation = MatchEvaluationService().evaluate(
        {
            "cv_profile": cv.model_dump(mode="python"),
            "position_profile": duplicate_position.model_dump(mode="python"),
        },
        include_semantic=False,
    )
    duplicate_groups = {
        item.group_id: item for item in duplicate_evaluation.requirement_group_results
    }
    assert duplicate_groups["standard-route:route-b"].child_result_ids == ROUTE_B
    assert duplicate_groups["standard-route:route-b"].score == 1.0


def test_standard_position_selects_route_a_when_cv_only_covers_route_a(
    ready_cv_json, ready_position_json
):
    cv, position = _models(
        ready_cv_json,
        ready_position_json,
        (
            ("req-cuda", "CUDA"),
            ("req-vllm", "vLLM"),
            ("req-sglang", "SGLang"),
        ),
    )
    effective, selection = prepare_effective_position(
        cv, position, MatchingAlgorithmConfig(), target_type="standard_position"
    )

    assert selection is not None
    assert selection.required_requirement_ids == ROUTE_A
    assert tuple(item.requirement_id for item in effective.required_skills) == ROUTE_A


def test_standard_position_tie_break_is_stable_by_route_id(
    ready_cv_json, ready_position_json
):
    cv, position = _models(
        ready_cv_json,
        ready_position_json,
        (
            ("req-cuda", "CUDA"),
            ("req-vllm", "vLLM"),
            ("req-megatron", "Megatron-LM"),
            ("req-rlhf", "RLHF"),
        ),
    )
    selection = prepare_effective_position(
        cv, position, MatchingAlgorithmConfig(), target_type="standard_position"
    )[1]

    assert selection is not None
    assert selection.required_requirement_ids == ("req-cuda", "req-vllm", "req-sglang")
    assert selection.score == 2 / 3


def test_chinese_skill_names_do_not_collide_during_route_evaluation(
    ready_cv_json, ready_position_json
):
    position_payload = deepcopy(ready_position_json)
    position_payload.update(
        {
            "position_id": "position_chinese_route_fixture",
            "canonical_position_id": "position_chinese_route",
            "canonical_title": "大模型算法工程师",
            "core_responsibilities": [],
            "hard_conditions": [],
            "preferred_skills": [],
            "required_skills": [
                {
                    "requirement_id": requirement_id,
                    "skill_id": f"skill-{requirement_id.removeprefix('req-')}",
                    "canonical_name": name,
                    "required_level": "working",
                    "importance": 1.0,
                    "resolution_status": "resolved",
                    "evidence_refs": [_evidence(f"jd:{requirement_id}", name)],
                }
                for requirement_id, name in (
                    ("req-sft", "监督微调"),
                    ("req-nlp", "自然语言处理"),
                )
            ],
            "requirement_graph": {
                "graph_version": SPECIALTY_ROUTE_GRAPH_VERSION,
                "status": "complete",
                "groups": [
                    {
                        "requirement_group_id": "standard-route:sft",
                        "group_type": "must",
                        "priority": "required",
                        "children": [
                            {
                                "node_type": "requirement_ref",
                                "ref_id": "req-sft",
                                "aspect": "监督微调",
                            }
                        ],
                        "evidence": _evidence("jd:route:sft", "监督微调"),
                        "confidence": 1.0,
                    },
                    {
                        "requirement_group_id": "standard-route:nlp",
                        "group_type": "must",
                        "priority": "required",
                        "children": [
                            {
                                "node_type": "requirement_ref",
                                "ref_id": "req-nlp",
                                "aspect": "自然语言处理",
                            }
                        ],
                        "evidence": _evidence("jd:route:nlp", "自然语言处理"),
                        "confidence": 1.0,
                    },
                    {
                        "requirement_group_id": "standard-route-root:chinese",
                        "group_type": "one_of",
                        "priority": "required",
                        "children": [
                            {
                                "node_type": "group_ref",
                                "ref_id": "standard-route:sft",
                            },
                            {
                                "node_type": "group_ref",
                                "ref_id": "standard-route:nlp",
                            },
                        ],
                        "evidence": _evidence("jd:route:root", "监督微调或自然语言处理"),
                        "confidence": 1.0,
                    },
                ],
                "unresolved_items": [],
            },
        }
    )
    cv = CVMatchProfile.model_validate(
        _cv_payload(ready_cv_json, (("req-sft", "监督微调"),))
    )
    position = PositionMatchProfile.model_validate(position_payload)

    effective, selection = prepare_effective_position(
        cv,
        position,
        MatchingAlgorithmConfig(),
        target_type="standard_position",
    )

    assert selection is not None
    assert selection.route_id == "standard-route:sft"
    assert selection.score == 1.0
    assert selection.required_requirement_ids == ("req-sft",)
    assert tuple(item.requirement_id for item in effective.required_skills) == (
        "req-sft",
    )

    evaluation = MatchEvaluationService().evaluate(
        {
            "cv_profile": cv.model_dump(mode="python"),
            "position_profile": position.model_dump(mode="python"),
            "target_type": "standard_position",
        },
        include_semantic=False,
    )
    groups = {item.group_id: item for item in evaluation.requirement_group_results}
    assert groups["standard-route:sft"].status == "satisfied"
    assert groups["standard-route:sft"].covered_result_ids == ("req-sft",)
    assert groups["standard-route-root:chinese"].status == "satisfied"
    assert groups["standard-route-root:chinese"].covered_result_ids == ("req-sft",)


def test_exact_jd_graph_and_enterprise_target_skip_specialty_route_selection(
    ready_cv_json, ready_position_json
):
    cv, position = _models(ready_cv_json, ready_position_json)
    config = MatchingAlgorithmConfig()
    exact_position = position.model_copy(
        update={
            "requirement_graph": position.requirement_graph.model_copy(
                update={"graph_version": "requirement-graph.v1"}
            )
        }
    )
    assert prepare_effective_position(
        cv, exact_position, config, target_type="standard_position"
    ) == (exact_position, None)
    assert prepare_effective_position(
        cv, position, config, target_type="enterprise_job"
    ) == (position, None)


def _task_service(storage: InMemoryPersistence) -> EvaluationTaskService:
    evaluation = MatchEvaluationService()
    return EvaluationTaskService(
        storage.unit_of_work,
        evaluation,
        LearningPathService(evaluation),
    )


def test_specialty_route_graph_version_changes_task_idempotency_identity(
    ready_cv_json, ready_position_json
):
    cv, position = _models(ready_cv_json, ready_position_json)
    legacy_flat_position = position.model_copy(update={"requirement_graph": None})
    storage = InMemoryPersistence()
    service = _task_service(storage)

    old_task = service.submit(
        {
            "cv_profile": cv.model_dump(mode="python"),
            "position_profile": legacy_flat_position.model_dump(mode="python"),
            "target_type": "standard_position",
        },
        "same-idempotency-key",
        "tenant-a",
        execute_immediately=False,
    )
    new_task = service.submit(
        {
            "cv_profile": cv.model_dump(mode="python"),
            "position_profile": position.model_dump(mode="python"),
            "target_type": "standard_position",
        },
        "same-idempotency-key",
        "tenant-a",
        execute_immediately=False,
    )

    assert old_task.created is True
    assert old_task.task.versions.position_requirement_graph_version == (
        POSITION_REQUIREMENT_GRAPH_NOT_APPLICABLE
    )
    assert new_task.created is True
    assert new_task.task.task_id != old_task.task.task_id
    assert new_task.task.versions.position_requirement_graph_version == (
        SPECIALTY_ROUTE_GRAPH_VERSION
    )
    assert new_task.task.versions.signature != old_task.task.versions.signature
    assert SPECIALTY_ROUTE_GRAPH_VERSION in new_task.task.versions.signature


def test_same_specialty_route_graph_version_remains_idempotent(
    ready_cv_json, ready_position_json
):
    cv, position = _models(ready_cv_json, ready_position_json)
    storage = InMemoryPersistence()
    service = _task_service(storage)
    payload = {
        "cv_profile": cv.model_dump(mode="python"),
        "position_profile": position.model_dump(mode="python"),
        "target_type": "standard_position",
    }

    first = service.submit(
        payload,
        "same-specialty-key",
        "tenant-a",
        execute_immediately=False,
    )
    replay = service.submit(
        payload,
        "same-specialty-key",
        "tenant-a",
        execute_immediately=False,
    )

    assert first.created is True
    assert replay.created is False
    assert replay.task == first.task
    assert replay.task.versions.position_requirement_graph_version == (
        SPECIALTY_ROUTE_GRAPH_VERSION
    )


def test_reading_specialty_route_result_does_not_mark_it_stale(
    ready_cv_json, ready_position_json
):
    cv, position = _models(ready_cv_json, ready_position_json)
    storage = InMemoryPersistence()
    service = _task_service(storage)

    submitted = service.submit(
        {
            "cv_profile": cv.model_dump(mode="python"),
            "position_profile": position.model_dump(mode="python"),
            "target_type": "standard_position",
        },
        "specialty-route-report",
        "tenant-a",
    )

    result = service.get_evaluation(
        submitted.task.evaluation_id, "tenant-a"
    ).result

    assert result is not None
    assert result.stale is False
    assert result.stale_reason_codes == ()
