from tests.factories import approve_build_tasks, valid_build


def publish(client, build_run_id, headers):
    response = client.post(
        f"/api/v1/graph/build-runs/{build_run_id}/publish",
        json={},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_relation_statistics_summary_explanation_and_version_queries(
    client, db, auth_headers
):
    headers = auth_headers()
    build = valid_build(client, db, headers)
    summary = build["summary"]
    assert summary["input"]["samples"] == 1
    assert summary["valid"]["samples"] == 1
    assert summary["deduplication"] == {"samples": 1, "duplicates": 0}
    assert summary["excluded"]["samples"] == 0
    assert summary["manual_modifications"]["relations"] == 0

    version = publish(client, build["build_run_id"], headers)
    response = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/relations",
        params={"page": 1, "page_size": 1, "min_weight": 0},
    )
    assert response.status_code == 200
    page = response.json()["data"]
    assert page["version_id"] == version["version_id"]
    assert page["is_current"] is True
    assert page["total"] == 1
    statistics = page["items"][0]["statistics"]
    assert statistics["supporting_jd_count"] == 1
    assert statistics["deduplicated_jd_count"] == 1
    assert statistics["enterprise_count"] == 1
    assert statistics["source_count"] == 1
    assert statistics["evidence_count"] == 1
    assert statistics["first_seen_at"] == statistics["last_seen_at"]
    assert statistics["raw_frequency"] == 1
    assert statistics["quality_adjusted_frequency"] > 0

    relation_id = page["items"][0]["relation_id"]
    explanation = client.get(
        f"/api/v1/relations/{relation_id}/explanation",
        params={"version_id": version["version_id"]},
    )
    assert explanation.status_code == 200
    value = explanation.json()["data"]
    assert value["skill_id"] == "SKILL_PYTHON"
    assert len(value["sources"]) == 1
    assert len(value["evidence"]) == 1
    assert value["weight_basis"]["final"] == page["items"][0]["final_weight"]
    assert "frequency_delta" in value["quality_impact"]

    filtered = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/relations",
        params={"version_id": version["version_id"], "skill_id": "missing"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"]["total"] == 0


def test_business_diff_and_manual_history(client, db, auth_headers):
    headers = auth_headers()
    first_build = valid_build(client, db, headers)
    first = publish(client, first_build["build_run_id"], headers)
    draft = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/drafts",
        json={"base_version_id": first["version_id"]},
        headers=headers,
    ).json()["data"]
    graph = client.get(
        f"/api/v1/graph/build-runs/{draft['draft_id']}/graph",
        headers=headers,
    ).json()["data"]
    relation = graph["skill_relations"][0]
    edited = client.post(
        f"/api/v1/relations/{relation['relation_id']}/modify",
        json={
            "build_run_id": draft["draft_id"],
            "position_id": "BACKEND_ENGINEER",
            "expected_revision": relation["revision"],
            "weight": 0.91,
            "confidence": 0.92,
            "importance_level": "core",
            "reason": "business diff test",
        },
        headers=headers,
    )
    assert edited.status_code == 200
    approve_build_tasks(client, draft["draft_id"], headers)
    second = publish(client, draft["draft_id"], headers)

    build_status = client.get(
        f"/api/v1/graph/build-runs/{draft['draft_id']}",
        headers=headers,
    ).json()["data"]
    assert build_status["summary"]["manual_modifications"] == {
        "relations": 1,
        "fields": 3,
        "events": 1,
    }

    diff = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/graph/versions/diff",
        params={
            "from_version_id": first["version_id"],
            "to_version_id": second["version_id"],
        },
    )
    assert diff.status_code == 200
    value = diff.json()["data"]
    assert value["summary"]["changed"] == 1
    change = value["changed"][0]
    assert {"weight", "confidence", "importance_level"} <= set(
        change["business_changes"]
    )
    assert "manual_modification" in change["change_sources"]

    current = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/relations",
    ).json()["data"]
    historical = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/relations",
        params={"version_id": first["version_id"]},
    ).json()["data"]
    assert current["version_id"] == second["version_id"]
    assert current["items"][0]["final_weight"] == 0.91
    assert historical["version_id"] == first["version_id"]
    assert historical["is_current"] is False
    assert historical["items"][0]["final_weight"] != 0.91

    current_relation_id = current["items"][0]["relation_id"]
    explanation = client.get(
        f"/api/v1/relations/{current_relation_id}/explanation",
        params={"version_id": second["version_id"]},
    ).json()["data"]
    assert len(explanation["manual_modification_history"]) == 1
