from sqlalchemy import select

from app.models import (
    AuditLog,
    GraphBuildRun,
    Skill,
    SkillAlias,
    SkillClassification,
    SkillTaxonomyNode,
    StandardPosition,
)


def test_capability_catalog_skill_snapshot_is_service_only_and_audited(
    client, db, auth_headers, integration_service_headers
):
    payload = {
        'contract_version': 'capability-skill-snapshot.v1',
        'skill_id': 'MAIN_SKILL_1',
        'canonical_name': 'Python',
        'category_code': 'LANGUAGE',
        'subcategory_code': 'BACKEND',
        'aliases': ['Py', 'Python3'],
        'status': 'active',
    }
    denied = client.put(
        '/api/v1/integrations/catalog/skills/MAIN_SKILL_1',
        json=payload,
        headers=auth_headers('admin'),
    )
    assert denied.status_code == 403
    headers = dict(integration_service_headers)
    headers.update({
        'X-Main-User-Id': 'main-admin-id',
        'X-Main-User-Role': 'admin',
        'X-Trace-Id': 'req_skill_snapshot',
    })
    response = client.put(
        '/api/v1/integrations/catalog/skills/MAIN_SKILL_1',
        json=payload,
        headers=headers,
    )
    assert response.status_code == 200
    skill = db.scalar(select(Skill).where(Skill.skill_id == 'MAIN_SKILL_1'))
    assert skill.canonical_name == 'Python'
    assert skill.category_code == 'LANGUAGE'
    aliases = db.scalars(
        select(SkillAlias.alias).where(SkillAlias.skill_id == 'MAIN_SKILL_1')
    ).all()
    assert set(aliases) == {'Py', 'Python3'}
    audit = db.scalar(select(AuditLog).where(
        AuditLog.action == 'import_capability_skill_snapshot'
    ))
    assert audit.trace_id == 'req_skill_snapshot'
    assert audit.after_snapshot['integration_context'] == {
        'main_user_id': 'main-admin-id',
        'main_user_role': 'admin',
    }


def test_capability_catalog_v2_persists_multidimensional_classifications(
    client, db, integration_service_headers
):
    headers = dict(integration_service_headers)
    headers.update({
        'X-Main-User-Id': 'main-admin-id',
        'X-Main-User-Role': 'admin',
        'X-Trace-Id': 'req_skill_snapshot_v2',
    })
    payload = {
        'contract_version': 'capability-skill-snapshot.v2',
        'skill_id': 'LANG_PYTHON',
        'canonical_name': 'Python',
        'aliases': ['Python3'],
        'taxonomy_version': 'sha256:' + 'a' * 64,
        'classifications': [
            {'facet': 'concept_class', 'code': 'technology', 'name_zh': '技术实体', 'name_en': 'Technology', 'is_primary': True},
            {'facet': 'technology_kind', 'code': 'language', 'name_zh': '编程与查询语言', 'name_en': 'Language', 'is_primary': True},
            {'facet': 'domain', 'code': 'software_engineering', 'name_zh': '软件工程', 'name_en': 'Software engineering', 'is_primary': True},
            {'facet': 'domain', 'code': 'ai_intelligent_systems', 'name_zh': '人工智能与智能系统', 'name_en': 'AI', 'is_primary': False},
        ],
        'status': 'active',
    }
    response = client.put(
        '/api/v2/integrations/catalog/skills/LANG_PYTHON',
        json=payload,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    skill = db.scalar(select(Skill).where(Skill.skill_id == 'LANG_PYTHON'))
    assert skill.category_code is None
    assert skill.taxonomy_version == payload['taxonomy_version']
    relations = db.scalars(
        select(SkillClassification).where(
            SkillClassification.skill_id == 'LANG_PYTHON'
        )
    ).all()
    assert len(relations) == 4
    nodes = db.scalars(select(SkillTaxonomyNode)).all()
    assert {(node.facet, node.code) for node in nodes} >= {
        ('concept_class', 'technology'),
        ('technology_kind', 'language'),
        ('domain', 'software_engineering'),
    }


def test_integration_document_upsert_and_normalized_import(
    client, db, integration_service_user, integration_service_headers
):
    headers = dict(integration_service_headers)
    headers.update({
        "X-Main-User-Id": "main-admin-id",
        "X-Main-User-Role": "admin",
        "X-Trace-Id": "req_integration_audit",
    })
    first = client.put(
        "/api/v1/integrations/jds/MAIN_JD_1",
        json={"document_id": "MAIN_JD_1", "raw_text": "Python工程师\n熟悉 Python"},
        headers=headers,
    )
    second = client.put(
        "/api/v1/integrations/jds/MAIN_JD_1",
        json={"document_id": "MAIN_JD_1", "raw_text": "Python工程师\n精通 Python"},
        headers=headers,
    )
    assert first.status_code == second.status_code == 200
    document = client.get("/api/v1/jds/MAIN_JD_1", headers=headers).json()["data"]
    assert document["raw_text"].endswith("精通 Python")

    raw_text = document["raw_text"]
    title_start = raw_text.index("Python工程师")
    skill_start = raw_text.rindex("Python")
    extraction = {
        "schema_version": "v2", "document_id": "MAIN_JD_1",
        "job_title": {
            "text": "Python工程师",
            "evidence": {
                "source_id": "MAIN_JD_1", "quote": "Python工程师",
                "start": title_start, "end": title_start + len("Python工程师"),
                "alignment": "exact", "occurrence_index": 0,
            },
        },
        "responsibilities": [],
        "requirements": [{
            "requirement_id": "REQ1", "kind": "skill", "modality": "required",
            "items": [{"name": "Python", "item_type": "language"}],
            "evidence": {
                "source_id": "MAIN_JD_1", "quote": "Python", "start": skill_start,
                "end": skill_start + 6, "alignment": "exact", "occurrence_index": 1,
            },
        }],
        "company_facts": [], "employment_facts": [],
    }
    assert client.post(
        "/api/v1/jds/MAIN_JD_1/extraction-result/import",
        json=extraction, headers=headers,
    ).status_code == 200
    assert client.post(
        "/api/v1/jds/MAIN_JD_1/extraction-result/align", headers=headers
    ).status_code == 200

    normalized = {
        "schema_version": "v2", "document_id": "MAIN_JD_1",
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": "Python工程师",
            "position_code": None,
            "position_name": None,
            "candidate_positions": [],
            "career_level": None,
            "leadership_scope": None,
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": [],
            "confidence": 0.0,
            "classification_status": "catalog_gap",
            "review_reason_codes": ["NO_SUITABLE_POSITION"],
            "evidence_refs": ["REQ1"],
            "classification_policy_version": "position-classifier.v3.0",
        },
        "normalized_requirements": [{
            "requirement_id": "REQ1", "kind": "skill",
            "normalized_skills": [{
                "source_name": "Python", "skill_id": None, "canonical_name": None,
                "category_code": None, "subcategory_code": None,
                "resolution_status": "unresolved",
                "resolution_source": "explicit_mapping",
            }],
        }],
        "salary": None,
        "unresolved_items": [{
            "source_name": "Python", "item_type": "skill",
            "reason": "catalog mapping required",
        }],
    }
    imported = client.post(
        "/api/v1/jds/MAIN_JD_1/normalized-result/import",
        json=normalized, headers=headers,
    )
    assert imported.status_code == 200
    stored = client.get(
        "/api/v1/jds/MAIN_JD_1/normalized-result", headers=headers
    ).json()["data"]
    assert stored["schema_version"] == "v2"
    assert stored["normalized_requirements"][0]["requirement_id"] == "REQ1"
    assert stored["normalized_requirements"][0]["normalized_skills"][0][
        "resolution_source"
    ] == "explicit_mapping"
    audit = db.scalar(select(AuditLog).where(
        AuditLog.action == "import_normalization_v2"
    ))
    assert audit.actor_id == integration_service_user.id
    assert audit.trace_id == "req_integration_audit"
    assert audit.after_snapshot["integration_context"] == {
        "main_user_id": "main-admin-id", "main_user_role": "admin"
    }


def test_build_run_detail_endpoint(client, db, auth_headers):
    position = StandardPosition(
        position_id="POS_INTEGRATION", name="集成岗位",
        category_code="TEST", status="active",
    )
    db.add(position); db.flush()
    run = GraphBuildRun(
        position_id=position.position_id, status="succeeded",
        config_snapshot={"minimum_valid_samples": 1},
        summary={"included_samples": 1},
    )
    db.add(run); db.commit()
    response = client.get(
        f"/api/v1/graph/build-runs/{run.id}", headers=auth_headers("developer")
    )
    assert response.status_code == 200
    assert response.json()["data"]["position_id"] == "POS_INTEGRATION"
    assert response.json()["data"]["status"] == "succeeded"
    assert response.json()["data"]["build_version"] == 1


def test_build_versions_increment_independently_for_each_position(
    client, db, auth_headers
):
    positions = [
        StandardPosition(
            position_id=position_id,
            name=name,
            category_code="TEST",
            status="active",
        )
        for position_id, name in (
            ("POS_VERSION_A", "版本岗位甲"),
            ("POS_VERSION_B", "版本岗位乙"),
        )
    ]
    db.add_all(positions)
    db.flush()
    runs = [
        GraphBuildRun(
            position_id=position_id,
            status="succeeded",
            config_snapshot={"minimum_valid_samples": 1},
            summary={"included_samples": 1},
        )
        for position_id in (
            "POS_VERSION_A",
            "POS_VERSION_B",
            "POS_VERSION_A",
        )
    ]
    for run in runs:
        db.add(run)
        db.flush()
    db.commit()

    response_a = client.get(
        "/api/v1/positions/POS_VERSION_A/graph/build-runs",
        headers=auth_headers("developer"),
    )
    response_b = client.get(
        "/api/v1/positions/POS_VERSION_B/graph/build-runs",
        headers=auth_headers("developer"),
    )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert [item["build_version"] for item in response_a.json()["data"]] == [2, 1]
    assert [item["build_version"] for item in response_b.json()["data"]] == [1]
    assert [item["id"] for item in response_a.json()["data"]] == [runs[2].id, runs[0].id]
