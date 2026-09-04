import pytest

from app.application.errors import ConcurrentReviewTaskWrite
from app.domain.publishing import (
    PublishGateFacts,
    RelationGateFact,
    ReviewTaskGateFact,
    evaluate_publish_gate,
)
from app.domain.review_tasks import (
    ReviewReasonSet,
    ReviewTaskDedupCommand,
    ReviewTaskDedupFacts,
    ReviewTaskDedupKey,
    ReviewTaskFacts,
    ReviewTaskMergePlan,
    decide_review_task_dedup,
    is_open_review_status,
    is_terminal_review_status,
)
from app.infrastructure.sqlalchemy.repository_adapters import (
    SqlAlchemyReviewTaskRepository,
)
from app.models import ReviewTask


def _command(*reasons: str) -> ReviewTaskDedupCommand:
    key = ReviewTaskDedupKey("position_skill_relation", "7", 3)
    return ReviewTaskDedupCommand(
        key,
        ReviewReasonSet(reasons),
        {"incoming": "metadata"},
    )


@pytest.mark.parametrize("status", ["pending", "claimed", "modified"])
def test_open_review_task_is_reused_without_lifecycle_regression(status):
    command = _command("z_reason", "a_reason")
    facts = ReviewTaskDedupFacts(
        command.key,
        (
            ReviewTaskFacts(
                11,
                command.key.object_type,
                command.key.object_id,
                command.key.build_run_id,
                status,
                42,
                {"reason": "original", "reasons": ["original"], "owner": "kept"},
            ),
        ),
    )

    decision = decide_review_task_dedup(facts, command)

    assert decision.action == "merge"
    assert decision.merge_plan is not None
    assert decision.merge_plan.target_status == status
    assert decision.merge_plan.target_assignee_id == 42
    assert decision.merge_plan.payload == {
        "incoming": "metadata",
        "reason": "original",
        "reasons": ["a_reason", "original", "z_reason"],
        "owner": "kept",
    }


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_terminal_review_task_does_not_prevent_new_task(status):
    command = _command("second_review")
    facts = ReviewTaskDedupFacts(
        command.key,
        (
            ReviewTaskFacts(
                11,
                command.key.object_type,
                command.key.object_id,
                command.key.build_run_id,
                status,
                42,
                {"reason": "finished", "reasons": ["finished"]},
            ),
        ),
    )

    decision = decide_review_task_dedup(facts, command)

    assert decision.action == "create"
    assert decision.new_task_plan is not None
    assert decision.new_task_plan.status == "pending"
    assert decision.new_task_plan.payload["reasons"] == ["second_review"]


def test_review_lifecycle_and_reason_invariants_are_centralized():
    assert all(is_open_review_status(value) for value in ("pending", "claimed", "modified"))
    assert all(is_terminal_review_status(value) for value in ("approved", "rejected"))
    with pytest.raises(ValueError, match="non-empty"):
        ReviewReasonSet(())


@pytest.mark.parametrize(
    ("status", "blocked"),
    [
        ("pending", True),
        ("claimed", True),
        ("modified", True),
        ("approved", False),
        ("auto_accepted", False),
        ("rejected", False),
        ("excluded", False),
    ],
)
def test_publish_gate_uses_domain_review_lifecycle(status, blocked):
    result = evaluate_publish_gate(
        PublishGateFacts(
            "approved",
            1,
            1,
            True,
            (),
            (RelationGateFact(1, "approved", 1.0, 0.0),),
            (ReviewTaskGateFact(9, "evidence", status),),
            0,
            0,
            0,
            0,
        )
    )

    assert ("open_review_tasks" in {error.rule for error in result.errors}) is blocked
    assert result.open_review_task_count == int(blocked)


def test_repository_loads_facts_and_applies_explicit_merge_plan(db):
    task = ReviewTask(
        object_type="position_skill_relation",
        object_id="7",
        build_run_id=None,
        status="claimed",
        assignee_id=None,
        payload={"reason": "original", "reasons": ["original"]},
    )
    db.add(task)
    db.flush()
    repository = SqlAlchemyReviewTaskRepository(db)
    key = ReviewTaskDedupKey("position_skill_relation", "7", None)

    facts = repository.load_review_task_dedup_facts(key)
    assert facts.matching_tasks[0].status == "claimed"
    result = repository.apply_review_task_merge_plan(
        ReviewTaskMergePlan(
            task.id,
            "claimed",
            None,
            "claimed",
            None,
            {"reason": "original", "reasons": ["new", "original"]},
        )
    )

    db.refresh(task)
    assert result.status == "claimed"
    assert task.assignee_id is None
    assert task.payload["reasons"] == ["new", "original"]


def test_repository_merge_plan_uses_status_and_assignee_cas(db):
    task = ReviewTask(
        object_type="position_skill_relation",
        object_id="7",
        build_run_id=None,
        status="modified",
        assignee_id=None,
        payload={},
    )
    db.add(task)
    db.flush()
    repository = SqlAlchemyReviewTaskRepository(db)

    with pytest.raises(ConcurrentReviewTaskWrite):
        repository.apply_review_task_merge_plan(
            ReviewTaskMergePlan(
                task.id,
                "claimed",
                None,
                "claimed",
                None,
                {"reasons": ["new"]},
            )
        )
