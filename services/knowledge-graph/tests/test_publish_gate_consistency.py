import pytest

from app.application.publish_gate_mapper import publish_gate_result
from app.domain.publishing import evaluate_publish_gate
from app.infrastructure.sqlalchemy.graph_persistence import load_publish_version_facts
from app.models import GraphBuildRun, ReviewTask
from tests.factories import valid_build


def _query_gate(client, run_id, headers):
    response = client.get(
        f"/api/v1/graph/build-runs/{run_id}/publish-gate",
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["data"]


@pytest.mark.parametrize(
    ("status", "blocked"),
    [
        ("pending", True),
        ("claimed", True),
        ("modified", True),
        ("approved", False),
        ("rejected", False),
    ],
)
def test_query_gate_matches_domain_gate_for_review_lifecycle(
    client, db, auth_headers, status, blocked
):
    headers = auth_headers()
    build = valid_build(client, db, headers)
    run = db.get(GraphBuildRun, build["build_run_id"])
    task = ReviewTask(
        object_type="evidence",
        object_id=f"status-{status}",
        build_run_id=run.id,
        status=status,
        payload={},
    )
    db.add(task)
    db.commit()

    query_result = _query_gate(client, run.id, headers)
    facts = load_publish_version_facts(db, run)
    domain_result = publish_gate_result(evaluate_publish_gate(facts.gate))

    assert query_result == domain_result
    assert ("open_review_tasks" in {item["rule"] for item in query_result["errors"]}) is blocked
    assert query_result["open_review_task_count"] == int(blocked)
    if blocked:
        error = next(
            item for item in query_result["errors"] if item["rule"] == "open_review_tasks"
        )
        assert error["task_ids"] == [task.id]


@pytest.mark.parametrize(
    ("status", "publishable"),
    [
        ("succeeded", True),
        ("draft", True),
        ("reviewing", True),
        ("approved", True),
        ("pending", False),
        ("failed", False),
    ],
)
def test_query_build_status_range_and_message_come_from_domain(
    client, db, auth_headers, status, publishable
):
    headers = auth_headers()
    build = valid_build(client, db, headers)
    run = db.get(GraphBuildRun, build["build_run_id"])
    run.status = status
    db.commit()

    query_result = _query_gate(client, run.id, headers)
    domain_result = publish_gate_result(
        evaluate_publish_gate(load_publish_version_facts(db, run).gate)
    )
    status_errors = [
        item for item in query_result["errors"] if item["rule"] == "build_status"
    ]

    assert query_result == domain_result
    assert bool(status_errors) is not publishable
    if status_errors:
        assert status_errors == [
            {"rule": "build_status", "message": "build status is not publishable"}
        ]


def test_query_and_publish_rejection_use_the_same_error_mapping(
    client, db, auth_headers
):
    headers = auth_headers()
    build = valid_build(client, db, headers)
    run_id = build["build_run_id"]
    db.add(
        ReviewTask(
            object_type="evidence",
            object_id="publish-consistency",
            build_run_id=run_id,
            status="pending",
            payload={},
        )
    )
    db.commit()

    query_errors = _query_gate(client, run_id, headers)["errors"]
    response = client.post(
        f"/api/v1/graph/build-runs/{run_id}/publish",
        json={},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["details"]["errors"] == query_errors
