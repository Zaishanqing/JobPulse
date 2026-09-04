from __future__ import annotations

from app.models import PositionCategory, Skill, SkillCategory, StandardPosition
from tests.factories import approve_build_tasks, prepare_jd, valid_build


def test_watermark_claim_mapping_projection_and_rollback_are_persistent(
    client, db, auth_headers
):
    admin = auth_headers()
    reviewer = auth_headers("reviewer")
    build = valid_build(client, db, admin, doc_id="INNOVATION_CLOSED_LOOP")
    run_id = build["build_run_id"]

    watermark = client.get(
        f"/api/v1/innovation/build-runs/{run_id}/watermark", headers=admin
    )
    assert watermark.status_code == 200
    watermark_data = watermark.json()["data"]
    assert watermark_data["validation_state"] == "absent"
    assert watermark_data["source_facts"][0]["source_kind"] == "legacy_local"

    candidate_body = {
        "candidate_id": "mapping-python-alias-1",
        "source_expression": "Python 开发",
        "proposed_skill_id": "SKILL_PYTHON",
        "signals": {
            "uncertainty": 0.4,
            "graph_impact": 0.8,
            "frequency": 0.7,
            "source_diversity": 0.5,
            "drift": 0.2,
        },
        "weights": {
            "uncertainty": 0.2,
            "graph_impact": 0.3,
            "frequency": 0.2,
            "source_diversity": 0.2,
            "drift": 0.1,
        },
        "model_version": "mapping-model.v1",
        "index_version": "catalog-index.v1",
        "mapping_policy_version": "mapping-policy.v1",
        "affected_contexts": [
            {"source_fact_id": "INNOVATION_CLOSED_LOOP", "requirement_id": "r1"}
        ],
    }
    created = client.post(
        "/api/v1/innovation/mapping-candidates",
        json=candidate_body,
        headers=admin,
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["status"] == "pending"
    invalid_weights = {**candidate_body, "candidate_id": "invalid-weights"}
    invalid_weights["weights"] = {**candidate_body["weights"], "drift": 0.2}
    invalid = client.post(
        "/api/v1/innovation/mapping-candidates",
        json=invalid_weights,
        headers=admin,
    )
    assert invalid.status_code == 422
    assert invalid.json()["details"]["error_code"] == "INVALID_MAPPING_CANDIDATE"
    prepare_jd(client, admin, doc_id="OTHER_FACT")
    unrelated = {
        **candidate_body,
        "candidate_id": "mapping-unrelated-r1",
        "affected_contexts": [
            {"source_fact_id": "OTHER_FACT", "requirement_id": "r1"}
        ],
    }
    assert client.post(
        "/api/v1/innovation/mapping-candidates",
        json=unrelated,
        headers=admin,
    ).status_code == 200
    reviewed = client.post(
        "/api/v1/innovation/mapping-candidates/mapping-python-alias-1/review",
        json={
            "expected_revision": 1,
            "decision": "accept",
            "reason": "exact catalog concept",
            "policy_version": "mapping-review.v1",
            "effective_scope": "affected_contexts",
        },
        headers=reviewer,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["data"]["revision"] == 2
    stale_review = client.post(
        "/api/v1/innovation/mapping-candidates/mapping-python-alias-1/review",
        json={
            "expected_revision": 1,
            "decision": "accept",
            "reason": "stale duplicate",
            "policy_version": "mapping-review.v1",
            "effective_scope": "affected_contexts",
        },
        headers=reviewer,
    )
    assert stale_review.status_code == 409
    listed = client.get(
        "/api/v1/innovation/mapping-candidates?status=accepted",
        headers=reviewer,
    ).json()["data"]
    assert [item["candidate_id"] for item in listed] == ["mapping-python-alias-1"]

    published = client.post(
        f"/api/v1/graph/build-runs/{run_id}/publish", json={}, headers=admin
    )
    assert published.status_code == 200, published.text
    version_id = published.json()["data"]["version_id"]
    claims = client.get(
        f"/api/v1/innovation/graph-versions/{version_id}/claims", headers=admin
    ).json()["data"]
    assert len(claims) == 1
    assert claims[0]["evidence"][0]["exact"] is True
    assert claims[0]["source_kind"] == "legacy_local"

    projection = client.post(
        f"/api/v1/innovation/graph-versions/{version_id}/projection/rebuild",
        json={"projection_version": "projection.v1"},
        headers=admin,
    )
    assert projection.status_code == 200, projection.text
    stored_projection = client.get(
        f"/api/v1/innovation/graph-versions/{version_id}/projection",
        headers=admin,
    ).json()["data"]
    assert stored_projection["source_version"] == projection.json()["data"][
        "projection"
    ]["manifest"]["source_version"]
    assert {edge["plane"] for edge in stored_projection["edges"]} == {
        "observed_unvalidated",
        "candidate",
    }
    mapping_edges = [
        edge
        for edge in stored_projection["edges"]
        if edge["edge_type"] == "PROPOSES_MAPPING"
    ]
    assert len(mapping_edges) == 1

    comparison = client.post(
        "/api/v1/innovation/watermarks/compare",
        json={
            "left_build_run_id": run_id,
            "right_build_run_id": run_id,
            "approved_catalog_crosswalk": False,
            "policy_replay_completed": False,
            "minimum_input_coverage": 1.0,
        },
        headers=admin,
    )
    assert comparison.status_code == 200
    assert comparison.json()["data"] == {
        "comparable": True,
        "status": "comparable",
        "reasons": [],
    }

    rollback = client.post(
        f"/api/v1/positions/BACKEND_ENGINEER/graph/versions/{version_id}/rollback",
        json={"reason": "verify lineage copy"},
        headers=admin,
    )
    assert rollback.status_code == 200, rollback.text
    rollback_version_id = rollback.json()["data"]["version_id"]
    rollback_claims = client.get(
        f"/api/v1/innovation/graph-versions/{rollback_version_id}/claims",
        headers=admin,
    ).json()["data"]
    assert len(rollback_claims) == len(claims)
    assert rollback_claims[0]["claim_id"] != claims[0]["claim_id"]


def test_dependency_analysis_persists_explicit_context_policy(
    client, db, auth_headers
):
    admin = auth_headers()
    db.add_all(
        [
            PositionCategory(code="TECH", name="技术"),
            SkillCategory(code="LANG", name="语言"),
            SkillCategory(code="DATA", name="数据"),
            StandardPosition(
                position_id="BACKEND_ENGINEER", name="后端工程师", category_code="TECH"
            ),
            Skill(
                skill_id="SKILL_PYTHON", canonical_name="Python", category_code="LANG"
            ),
            Skill(skill_id="SKILL_SQL", canonical_name="SQL", category_code="DATA"),
        ]
    )
    db.commit()
    raw = "后端工程师\n熟悉 Python SQL"
    assert client.post(
        "/api/v1/jds",
        json={
            "document_id": "DEPENDENCY_CONTEXT",
            "raw_text": raw,
            "source_type": "test",
            "source_name": "board-a",
            "enterprise_name": "enterprise-a",
        },
        headers=admin,
    ).status_code == 200
    extraction = {
        "document_id": "DEPENDENCY_CONTEXT",
        "job_title": {
            "text": "后端工程师",
            "evidence": {"source_id": "DEPENDENCY_CONTEXT", "quote": "后端工程师"},
        },
        "responsibilities": [],
        "requirements": [
            {
                "requirement_id": "skills-together",
                "kind": "skill",
                "modality": "required",
                "evidence": {
                    "source_id": "DEPENDENCY_CONTEXT",
                    "quote": "熟悉 Python SQL",
                },
                "items": [{"name": "Python"}, {"name": "SQL"}],
            }
        ],
        "company_facts": [],
        "employment_facts": [],
    }
    for path, body in (
        ("extraction-result/import", extraction),
        ("extraction-result/align", None),
    ):
        response = client.post(
            f"/api/v1/jds/DEPENDENCY_CONTEXT/{path}", json=body, headers=admin
        )
        assert response.status_code == 200, response.text
    normalized = client.post(
        "/api/v1/jds/DEPENDENCY_CONTEXT/normalize", headers=admin
    ).json()["data"]
    normalized["job_classification"] = {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": "position-taxonomy.v3.0.0",
        "source_title": "后端工程师",
        "position_code": "BACKEND_ENGINEER",
        "position_name": "后端工程师",
        "family_code": "SOFTWARE_ENGINEERING",
        "family_name": "软件工程与研发",
        "candidate_positions": [{"position_code": "BACKEND_ENGINEER", "score": 0.95}],
        "career_level": "mid",
        "leadership_scope": "none",
        "technology_focus_codes": [],
        "industry_context_codes": [],
        "observed_skill_domain_codes": ["software_engineering"],
        "confidence": 0.95,
        "classification_status": "resolved",
        "review_reason_codes": [],
        "evidence_refs": ["skills-together"],
        "classification_policy_version": "position-classifier.v3.0",
    }
    assert client.post(
        "/api/v1/jds/DEPENDENCY_CONTEXT/normalized-result/import",
        json=normalized,
        headers=admin,
    ).status_code == 200
    assert client.post(
        "/api/v1/jds/DEPENDENCY_CONTEXT/duplicate-check",
        json={},
        headers=admin,
    ).status_code == 200
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build", json={}, headers=admin
    ).json()["data"]
    approve_build_tasks(client, build["build_run_id"], admin)
    policy = {
        "minimum_joint_support": 1,
        "minimum_conditional_probability": 0.5,
        "minimum_source_diversity": 1,
        "minimum_enterprise_diversity": 1,
        "maximum_enterprise_share": 1.0,
        "bootstrap_iterations": 100,
        "confidence_level": 0.9,
        "minimum_stable_slices": 1,
    }
    analyzed = client.post(
        f"/api/v1/innovation/build-runs/{build['build_run_id']}/dependencies/analyze",
        json=policy,
        headers=admin,
    )
    assert analyzed.status_code == 200, analyzed.text
    stored = client.get(
        f"/api/v1/innovation/build-runs/{build['build_run_id']}/dependencies",
        headers=admin,
    )
    assert stored.status_code == 200
    assert stored.json()["data"]["policy"] == policy
