import pytest

from app.domain.publishing import (
    PublishGateFacts,
    RelationGateFact,
    ReviewTaskGateFact,
    evaluate_publish_gate,
)
from app.domain.review_tasks import (
    ReviewTaskCommand,
    ReviewTaskFacts,
    auto_review_allowed,
    decide_review_task_transition,
    requires_human_review,
)
from app.infrastructure.sqlalchemy.graph_persistence import _snapshot
from app.infrastructure.sqlalchemy.review_handlers import (
    GraphVersionReviewHandler,
    PositionRequirementReviewHandler,
    PositionTaskReviewHandler,
)
from app.models import (
    GraphBuildRun,
    PositionRequirementAggregateDraft,
    PositionTaskAggregateDraft,
)
from tests.factories import prepare_catalog, prepare_jd


def _rules(result):
    return {item.rule for item in result.errors}


def test_rejected_graph_version_review_blocks_publish():
    result = evaluate_publish_gate(
        PublishGateFacts(
            "approved",
            1,
            1,
            True,
            (),
            (RelationGateFact(1, "approved", 1.0, 0.0),),
            (ReviewTaskGateFact(1, "graph_version", "rejected"),),
            0,
            0,
            0,
            0,
        )
    )
    assert "graph_version_rejected" in _rules(result)
    assert result.hard_gate_allowed


def test_auto_accepted_relation_passes_gate():
    result = evaluate_publish_gate(
        PublishGateFacts(
            "approved",
            1,
            1,
            True,
            (),
            (RelationGateFact(1, "auto_accepted", 0.6, 0.0),),
            (),
            0,
            0,
            0,
            0,
        )
    )
    assert result.allowed


def test_excluded_relation_does_not_block_remaining_graph():
    result = evaluate_publish_gate(
        PublishGateFacts(
            "approved",
            1,
            1,
            True,
            (),
            (
                RelationGateFact(1, "approved", 1.0, 0.0),
                RelationGateFact(2, "excluded", 0.4, 0.2),
            ),
            (),
            0,
            0,
            0,
            0,
        )
    )
    assert result.allowed
    assert result.low_confidence_relation_count == 0


def test_requirement_only_build_passes_non_empty_graph():
    result = evaluate_publish_gate(
        PublishGateFacts(
            "approved",
            1,
            1,
            True,
            (),
            (),
            (),
            0,
            0,
            1,
            0,
        )
    )
    assert result.allowed
    assert result.requirement_profile_available
    assert not result.skill_profile_available
    assert not result.task_profile_available


def test_task_only_build_passes_non_empty_graph():
    result = evaluate_publish_gate(
        PublishGateFacts(
            "approved",
            1,
            1,
            True,
            (),
            (),
            (),
            0,
            0,
            0,
            1,
        )
    )
    assert result.allowed
    assert result.task_profile_available
    assert not result.skill_profile_available
    assert not result.requirement_profile_available


def test_empty_build_still_fails_non_empty_graph():
    result = evaluate_publish_gate(
        PublishGateFacts(
            "approved",
            1,
            1,
            True,
            (),
            (),
            (),
            0,
            0,
            0,
            0,
        )
    )
    assert "non_empty_graph" in _rules(result)
    assert not result.skill_profile_available
    assert not result.task_profile_available
    assert not result.requirement_profile_available


def test_auto_accept_is_a_terminal_policy_transition():
    task = ReviewTaskFacts(
        1,
        "position_requirement",
        "1",
        1,
        "pending",
        None,
        {"reasons": ["low_aggregate_confidence"]},
    )
    decision = decide_review_task_transition(
        task,
        ReviewTaskCommand(
            "auto_accept",
            7,
            "trace-1",
            "policy",
            {"policy_version": "review-policy.v1"},
        ),
    )
    assert decision.accepted
    assert decision.plan is not None
    assert decision.plan.transition.target_status == "auto_accepted"


def test_human_review_reasons_are_not_auto_acceptable():
    assert not requires_human_review(
        {"reasons": ["medium_or_low_confidence"]}
    )
    assert requires_human_review({"reasons": ["unknown_modality"]})


def test_auto_review_fails_closed_for_unknown_or_empty_reasons():
    assert not auto_review_allowed({})
    assert not auto_review_allowed({"reasons": ["mystery_reason"]})
    assert not auto_review_allowed(
        {"reasons": ["medium_or_low_confidence", "mystery_reason"]}
    )
    assert auto_review_allowed(
        {"reasons": ["medium_or_low_confidence"]}
    )
    assert not auto_review_allowed(
        {"reasons": ["medium_or_low_confidence"]},
        "review-policy.v999",
    )


def test_snapshot_excludes_rejected_and_excluded_aggregates(db):
    prepare_catalog(db)
    run = GraphBuildRun(
        position_id="BACKEND_ENGINEER",
        status="succeeded",
        config_snapshot={"minimum_valid_samples": 1},
        summary={"included_samples": 1},
    )
    db.add(run)
    db.flush()
    rejected = PositionRequirementAggregateDraft(
        build_run_id=run.id,
        kind="experience",
        payload={"text": "五年后端经验"},
        status="rejected",
    )
    included = PositionRequirementAggregateDraft(
        build_run_id=run.id,
        kind="experience",
        payload={"text": "三年分布式经验"},
        status="included",
    )
    excluded_task = PositionTaskAggregateDraft(
        build_run_id=run.id,
        payload={"text": "负责老旧系统维护"},
        status="excluded",
    )
    db.add_all([rejected, included, excluded_task])
    db.commit()

    snapshot = _snapshot(db, run, [])
    profile_texts = [
        item.get("text") for item in snapshot["requirement_profile"]
    ]
    task_texts = [item.get("text") for item in snapshot["responsibilities"]]
    assert "五年后端经验" not in profile_texts
    assert "三年分布式经验" in profile_texts
    assert "负责老旧系统维护" not in task_texts


def test_aggregate_review_handlers_mark_decision_status(db):
    prepare_catalog(db)
    run = GraphBuildRun(
        position_id="BACKEND_ENGINEER",
        status="succeeded",
        config_snapshot={"minimum_valid_samples": 1},
        summary={"included_samples": 1},
    )
    db.add(run)
    db.flush()
    requirement = PositionRequirementAggregateDraft(
        build_run_id=run.id,
        kind="experience",
        payload={"text": "需要拒绝的要求"},
    )
    task = PositionTaskAggregateDraft(
        build_run_id=run.id,
        payload={"text": "需要拒绝的职责"},
    )
    db.add_all([requirement, task])
    db.flush()

    PositionRequirementReviewHandler(db).apply(
        str(requirement.id), run.id, "rejected"
    )
    PositionTaskReviewHandler(db).apply(str(task.id), run.id, "excluded")
    assert requirement.status == "rejected"
    assert task.status == "excluded"


def test_graph_version_review_disposition_keeps_build_run_state(db):
    prepare_catalog(db)
    run = GraphBuildRun(
        position_id="BACKEND_ENGINEER",
        status="succeeded",
        config_snapshot={"minimum_valid_samples": 1},
        summary={"included_samples": 1},
    )
    db.add(run)
    db.commit()
    GraphVersionReviewHandler().apply("build", run.id, "rejected")
    assert run.status == "succeeded"


def test_aggregate_handler_rejects_object_from_another_build(db):
    prepare_catalog(db)
    run = GraphBuildRun(
        position_id="BACKEND_ENGINEER",
        status="succeeded",
        config_snapshot={"minimum_valid_samples": 1},
        summary={"included_samples": 1},
    )
    db.add(run)
    db.flush()
    requirement = PositionRequirementAggregateDraft(
        build_run_id=run.id,
        kind="experience",
        payload={"text": "本构建的要求"},
    )
    db.add(requirement)
    db.flush()
    with pytest.raises(Exception):
        PositionRequirementReviewHandler(db).apply(
            str(requirement.id), run.id + 1, "rejected"
        )
    assert requirement.status == "included"


def test_auto_review_build_applies_one_policy_version(
    client, db, auth_headers
):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={},
        headers=headers,
    ).json()["data"]
    run_id = build["build_run_id"]
    response = client.post(
        f"/api/v1/graph/build-runs/{run_id}/auto-review",
        json={
            "policy_version": "review-policy.v1",
            "reason": "policy calibration",
        },
        headers=headers,
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["policy_version"] == "review-policy.v1"
    assert "medium_or_low_confidence" in result["allowed_reasons"]
    assert result["auto_accepted_count"] > 0
    assert result["requires_human_count"] >= 0

    unknown = client.post(
        f"/api/v1/graph/build-runs/{run_id}/auto-review",
        json={
            "policy_version": "review-policy.v999",
            "reason": "unknown policy probe",
        },
        headers=headers,
    )
    assert unknown.status_code == 422

    tasks = client.get(
        f"/api/v1/review-tasks?build_run_id={run_id}&page_size=100",
        headers=headers,
    ).json()["data"]
    assert tasks
    assert all(task["build_run_id"] == run_id for task in tasks)
    overall = [
        task for task in tasks if task["object_type"] == "graph_version"
    ]
    assert overall and all(task["status"] == "auto_accepted" for task in overall)


def test_explicit_historical_build_run_returns_tasks(
    client, db, auth_headers
):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    first = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={},
        headers=headers,
    ).json()["data"]["build_run_id"]
    prepare_jd(client, headers, doc_id="JD2")
    second = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={},
        headers=headers,
    ).json()["data"]["build_run_id"]
    assert first != second

    tasks = client.get(
        f"/api/v1/review-tasks?build_run_id={first}&page_size=100",
        headers=headers,
    ).json()["data"]
    assert tasks
    assert all(task["build_run_id"] == first for task in tasks)
