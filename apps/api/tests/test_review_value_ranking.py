from datetime import datetime, timedelta, timezone
from dataclasses import asdict

from app.contexts.review_value_ranking import (
    ReviewRankInput,
    rank_review_task,
    review_wait_days,
)


def _rank(**overrides) -> dict:
    payload = {
        "task_id": "task-1",
        "status": "pending",
        "priority": "normal",
        "blocking": False,
        "uncertainty_count": 0,
        "impact_count": 0,
        "reuse_count": 0,
        "wait_days": 0.0,
        "estimated_review_cost": 1.0,
        "created_at": None,
    }
    payload.update(overrides)
    return {
        key: value
        for key, value in asdict(rank_review_task(ReviewRankInput(**payload))).items()
        if key != "task_id"
    }


def test_blocking_and_high_impact_task_ranks_above_quiet_task():
    blocking = _rank(
        blocking=True,
        uncertainty_count=4,
        impact_count=9,
        reuse_count=5,
    )
    quiet = _rank()

    assert blocking["priority_score"] > quiet["priority_score"]
    assert "blocking_release" in blocking["priority_reasons"]
    assert "uncertainty:4" in blocking["priority_reasons"]
    assert "impact:9" in blocking["priority_reasons"]
    assert "reuse:5" in blocking["priority_reasons"]
    assert blocking["blocking_state"] is True
    assert blocking["affected_subjects"] == ("9_subjects", "4_uncertain_items")


def test_no_value_signal_is_deterministic_and_explicit():
    result = _rank()

    assert result["priority_score"] == 0.0
    assert result["priority_reasons"] == ("no_value_signal",)
    assert result["affected_subjects"] == ()
    assert result["similar_task_count"] == 0
    assert result["method_version"] == "review-value-rank.v1"


def test_scores_are_capped_and_reason_decomposition_is_stable():
    saturated = _rank(
        blocking=True,
        uncertainty_count=100,
        impact_count=500,
        reuse_count=300,
    )

    assert saturated["priority_score"] == round(
        0.35 + 0.25 + 0.25 + 0.15, 6
    )
    assert list(saturated["priority_reasons"]) == [
        "blocking_release",
        "uncertainty:100",
        "impact:500",
        "reuse:300",
    ]


def test_wait_days_tiebreak_is_bounded_and_non_negative():
    created = datetime.now(timezone.utc) - timedelta(days=10)
    now = datetime.now(timezone.utc)

    assert round(review_wait_days(created, now=now), 6) == 10.0
    assert review_wait_days(None, now=now) == 0.0
    future = now + timedelta(days=1)
    assert review_wait_days(future, now=now) == 0.0
