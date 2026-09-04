from __future__ import annotations

from jobgraph_contracts import ReviewTaskV1

from app.main import app
from tests.factories import approve_build_tasks, prepare_catalog, prepare_jd


def _candidate_payload(candidate_id: str = "MAP_PYTHON") -> dict:
    return {
        "candidate_id": candidate_id,
        "source_expression": "Python",
        "proposed_skill_id": "SKILL_PYTHON",
        "signals": {
            "uncertainty": 0.2,
            "graph_impact": 0.9,
            "frequency": 0.8,
            "source_diversity": 0.7,
            "drift": 0.1,
        },
        "weights": {
            "uncertainty": 0.2,
            "graph_impact": 0.2,
            "frequency": 0.2,
            "source_diversity": 0.2,
            "drift": 0.2,
        },
        "model_version": "mapping-model-v1",
        "index_version": "catalog-index-v1",
        "mapping_policy_version": "mapping-policy-v1",
        "affected_contexts": [
            {"source_fact_id": "JD1", "requirement_id": "r1"}
        ],
    }


def test_build_is_durable_background_job_with_failure_and_retry(
    client, db, auth_headers, monkeypatch
):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    original_inline = app.state.settings.build_jobs_inline
    original_execute = app.state.build_job_runner.builds.execute
    app.state.settings.build_jobs_inline = False
    try:
        queued = client.post(
            "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
        )
        assert queued.status_code == 202
        job_id = queued.json()["data"]["job_id"]
        assert queued.json()["data"]["status"] == "queued"

        def fail_once(_command):
            raise RuntimeError("intentional worker failure")

        monkeypatch.setattr(app.state.build_job_runner.builds, "execute", fail_once)
        failed = app.state.build_job_runner.run_once(job_id)
        assert failed.status == "failed"
        status = client.get(
            f"/api/v1/graph/build-jobs/{job_id}", headers=headers
        ).json()["data"]
        assert status["error"]["code"] == "RuntimeError"

        monkeypatch.setattr(
            app.state.build_job_runner.builds, "execute", original_execute
        )
        retried = client.post(
            f"/api/v1/graph/build-jobs/{job_id}/retry", headers=headers
        )
        assert retried.status_code == 202
        assert retried.json()["data"]["status"] == "queued"
        completed = app.state.build_job_runner.run_once(job_id)
        assert completed.status == "succeeded"
        assert completed.build_run_id is not None
    finally:
        app.state.settings.build_jobs_inline = original_inline


def test_mapping_impact_preview_and_dependency_event(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    ).json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    version = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={"reason": "impact contract"},
        headers=headers,
    ).json()["data"]

    created = client.post(
        "/api/v1/innovation/mapping-candidates",
        json=_candidate_payload(),
        headers=headers,
    )
    assert created.status_code == 200
    reference = client.post(
        "/api/v1/dependency-references",
        json={
            "consumer_system": "matching",
            "reference_type": "match-result",
            "reference_id": "MATCH-1",
            "graph_version_id": version["version_id"],
            "metadata": {"contract_version": "position-profile.v2"},
        },
        headers=headers,
    )
    assert reference.status_code == 200

    review = {
        "expected_revision": 1,
        "decision": "accept",
        "reason": "verified mapping",
        "policy_version": "mapping-policy-v1",
        "effective_scope": "affected_contexts",
        "replacement_candidate_id": None,
    }
    preview = client.post(
        "/api/v1/innovation/mapping-candidates/MAP_PYTHON/impact-preview",
        json=review,
        headers=headers,
    ).json()["data"]
    assert preview["preview_only"] is True
    assert preview["build_runs"][0]["build_run_id"] == build["build_run_id"]
    assert preview["graph_versions"][0]["graph_version_id"] == version["version_id"]
    assert preview["downstream_references"][0]["reference_id"] == "MATCH-1"

    updated = client.post(
        "/api/v1/innovation/mapping-candidates/MAP_PYTHON/review",
        json=review,
        headers=headers,
    )
    assert updated.status_code == 200
    events = client.get(
        "/api/v1/dependency-events",
        params={"entity_type": "skill_mapping", "entity_id": "MAP_PYTHON"},
        headers=headers,
    ).json()["data"]
    assert len(events) == 1
    assert events[0]["change_kind"] == "mapping_accept"
    assert events[0]["impact"]["downstream_references"][0]["reference_id"] == "MATCH-1"


def test_review_contract_filters_and_atomic_batch(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    prepare_jd(client, headers)
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=headers
    ).json()["data"]
    response = client.get(
        "/api/v1/review-tasks",
        params={"task_kind": "position_skill_relation", "page": 1, "page_size": 10},
        headers=headers,
    )
    tasks = response.json()["data"]
    assert response.headers["X-Total-Count"] == str(len(tasks))
    assert tasks
    assert tasks[0]["contract_version"] == "review-task.v1"
    assert ReviewTaskV1.model_validate(tasks[0]).task_id == tasks[0]["task_id"]
    assert tasks[0]["task_kind"] == "position_skill_relation"
    assert "evidence" in tasks[0]["evidence_context"]
    task_ids = [task["id"] for task in tasks[:2]]
    batch = client.post(
        "/api/v1/review-tasks/batch",
        json={
            "contract_version": "review-batch.v1",
            "task_ids": task_ids,
            "action": "claim",
            "reason": "batch ownership",
        },
        headers=headers,
    )
    assert batch.status_code == 200
    assert set(batch.json()["data"]["statuses"]) == {str(value) for value in task_ids}
    assert build["build_run_id"]
