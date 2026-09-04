from sqlalchemy import select

from app.domain.policies import merged_relation_config
from app.domain.profile_thresholds import (
    DEFAULT_POSITION_PROFILE_THRESHOLDS,
    PositionProfileThresholdConfig,
    RequiredSkillGate,
    ResponsibilityThreshold,
    SkillImportanceThreshold,
    apply_position_profile_thresholds,
    build_config_version,
    classify_requirement_inflation,
    requirement_inflation_risk_level,
)
from app.domain.responsibility_topics import (
    merge_responsibility_topics,
    normalize_responsibility_topic,
)
from app.models import DuplicateCluster, GraphVersion, PositionSkillSupport, Skill
from tests.factories import approve_build_tasks, prepare_catalog

SKILL_IDS = {
    "Python": "SKILL_PYTHON",
    "SQL": "SKILL_SQL",
    "Docker": "SKILL_DOCKER",
    "Rare": "SKILL_RARE",
}


def _prepare_profile_jd(
    client,
    headers,
    doc_id,
    *,
    skills,
    responsibilities,
    source_name,
):
    quote_lines = [f"熟悉 {name}" for name, _ in skills]
    quote_lines.extend(f"负责 {text}" for text in responsibilities)
    raw = "后端工程师\n" + "\n".join(quote_lines) + "\n本科及以上"
    assert client.post(
        "/api/v1/jds",
        json={
            "document_id": doc_id,
            "raw_text": raw,
            "source_type": "test",
            "source_name": source_name,
            "enterprise_name": f"enterprise-{source_name}",
        },
        headers=headers,
    ).status_code == 200
    responsibilities_payload = [
        {
            "requirement_id": f"t{index}",
            "text": text,
            "evidence": {"source_id": doc_id, "quote": f"负责 {text}"},
        }
        for index, text in enumerate(responsibilities)
    ]
    requirements = [
        {
            "requirement_id": f"r{index}",
            "kind": "skill",
            "modality": modality,
            "evidence": {"source_id": doc_id, "quote": f"熟悉 {name}"},
            "items": [{"name": name}],
        }
        for index, (name, modality) in enumerate(skills)
    ]
    requirements.append(
        {
            "requirement_id": "r-education",
            "kind": "education",
            "modality": "required",
            "evidence": {"source_id": doc_id, "quote": "本科及以上"},
            "text": "本科及以上",
        }
    )
    payload = {
        "document_id": doc_id,
        "job_title": {
            "text": "后端工程师",
            "evidence": {"source_id": doc_id, "quote": "后端工程师"},
        },
        "responsibilities": responsibilities_payload,
        "requirements": requirements,
        "company_facts": [],
        "employment_facts": [],
    }
    assert client.post(
        f"/api/v1/jds/{doc_id}/extraction-result/import",
        json=payload,
        headers=headers,
    ).status_code == 200
    assert client.post(
        f"/api/v1/jds/{doc_id}/extraction-result/align",
        headers=headers,
    ).status_code == 200
    normalized_requirements = [
        {
            "requirement_id": f"r{index}",
            "kind": "skill",
            "normalized_skills": [
                {
                    "source_name": name,
                    "skill_id": SKILL_IDS[name],
                    "canonical_name": name,
                    "category_code": "LANG",
                    "subcategory_code": None,
                    "resolution_status": "resolved",
                    "resolution_source": "explicit_mapping",
                }
            ],
        }
        for index, (name, _modality) in enumerate(skills)
    ]
    normalized_requirements.append(
        {
            "requirement_id": "r-education",
            "kind": "education",
            "normalized_skills": [],
        }
    )
    normalized = {
        "schema_version": "v2",
        "document_id": doc_id,
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": "后端工程师",
            "position_code": "BACKEND_ENGINEER",
            "position_name": "后端工程师",
            "family_code": "SOFTWARE_ENGINEERING",
            "family_name": "软件工程与研发",
            "candidate_positions": [
                {"position_code": "BACKEND_ENGINEER", "score": 0.93}
            ],
            "career_level": "mid",
            "leadership_scope": "none",
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": ["software_engineering"],
            "confidence": 0.93,
            "classification_status": "resolved",
            "review_reason_codes": [],
            "evidence_refs": [
                f"r{index}" for index in range(len(skills))
            ] + ["r-education"],
            "classification_policy_version": "position-classifier.v3.0",
        },
        "normalized_requirements": normalized_requirements,
        "salary": None,
        "unresolved_items": [],
    }
    assert client.post(
        f"/api/v1/jds/{doc_id}/normalized-result/import",
        json=normalized,
        headers=headers,
    ).status_code == 200
    assert client.post(
        f"/api/v1/jds/{doc_id}/duplicate-check",
        json={},
        headers=headers,
    ).status_code == 200


def _publish_profile(client, db, headers):
    prepare_catalog(db)
    db.add_all(
        [
            Skill(skill_id="SKILL_SQL", canonical_name="SQL", category_code="LANG"),
            Skill(skill_id="SKILL_DOCKER", canonical_name="Docker", category_code="LANG"),
            Skill(skill_id="SKILL_RARE", canonical_name="Rare", category_code="LANG"),
        ]
    )
    db.commit()
    _prepare_profile_jd(
        client,
        headers,
        "JD1",
        skills=[
            ("Python", "required"),
            ("Python", "required"),
            ("SQL", "required"),
            ("Docker", "preferred"),
            ("Rare", "required"),
        ],
        responsibilities=["负责系统设计", "负责服务开发"],
        source_name="board-a",
    )
    _prepare_profile_jd(
        client,
        headers,
        "JD2",
        skills=[
            ("Python", "required"),
            ("SQL", "preferred"),
            ("Docker", "preferred"),
        ],
        responsibilities=["负责系统设计"],
        source_name="board-b",
    )
    _prepare_profile_jd(
        client,
        headers,
        "JD3",
        skills=[("Python", "preferred"), ("Docker", "bonus")],
        responsibilities=["负责系统设计", "负责性能优化"],
        source_name="board-c",
    )
    _prepare_profile_jd(
        client,
        headers,
        "JD4",
        skills=[("Docker", "required")],
        responsibilities=[],
        source_name="board-d",
    )
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={},
        headers=headers,
    ).json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    response = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={"reason": "thresholds"},
        headers=headers,
    )
    assert response.status_code == 200
    return db.scalar(select(GraphVersion))


def test_level_specific_thresholds_and_tiers():
    thresholds = DEFAULT_POSITION_PROFILE_THRESHOLDS
    skills = [
        {"skill_id": "CORE_OK", "final_importance_level": "core", "primary_modality": "required"},
        {"skill_id": "CORE_FILTERED", "final_importance_level": "core", "primary_modality": "required"},
        {"skill_id": "IMPORTANT_OK", "final_importance_level": "important", "primary_modality": "required"},
        {"skill_id": "IMPORTANT_FILTERED", "final_importance_level": "important", "primary_modality": "required"},
        {"skill_id": "SUPP_OK", "final_importance_level": "supplementary", "primary_modality": "required"},
        {"skill_id": "SUPP_FILTERED", "final_importance_level": "supplementary", "primary_modality": "required"},
        {"skill_id": "INVALID_LEVEL", "final_importance_level": "common", "primary_modality": "required"},
        {"skill_id": "INVALID_MODALITY", "final_importance_level": "core", "primary_modality": "unknown"},
    ]
    responsibilities = [
        {"aggregate_id": 1, "text": "core"},
        {"aggregate_id": 2, "text": "important"},
        {"aggregate_id": 3, "text": "supplementary"},
        {"aggregate_id": 4, "text": "below"},
    ]
    result = apply_position_profile_thresholds(
        skills,
        responsibilities,
        skill_supporting_jd_count={
            "CORE_OK": 4,
            "CORE_FILTERED": 2,
            "IMPORTANT_OK": 4,
            "IMPORTANT_FILTERED": 1,
            "SUPP_OK": 3,
            "SUPP_FILTERED": 1,
            "INVALID_LEVEL": 4,
            "INVALID_MODALITY": 4,
        },
        skill_required_jd_count={
            "CORE_OK": 4,
            "IMPORTANT_OK": 4,
            "SUPP_OK": 3,
        },
        responsibility_supporting_jd_count={
            "1": 4,
            "2": 3,
            "3": 2,
            "4": 1,
        },
        total_dedup_jd_count=21,
        thresholds=thresholds,
    )
    retained_skills = {item["skill_id"]: item for item in result.skill_relations}
    assert set(retained_skills) == {"CORE_OK", "IMPORTANT_OK", "SUPP_OK"}
    assert retained_skills["CORE_OK"]["profile_tier"] == "market_core"
    assert retained_skills["IMPORTANT_OK"]["profile_tier"] == "market_core"
    assert retained_skills["SUPP_OK"]["profile_tier"] == "specialty"
    assert result.skill_retained_by_level == {
        "core": 1,
        "important": 1,
        "supplementary": 1,
    }
    assert result.skill_filtered_by_level == {
        "core": 1,
        "important": 1,
        "supplementary": 1,
        "invalid": 2,
    }
    assert result.responsibility_retained_by_level == {
        "core": 1,
        "important": 1,
        "supplementary": 1,
    }
    assert result.responsibility_filtered_by_level == {
        "core": 0,
        "important": 0,
        "supplementary": 1,
    }


def test_requirement_strength_calibration_distinguishes_market_and_outliers():
    gate = DEFAULT_POSITION_PROFILE_THRESHOLDS.required_skill_gate

    market = classify_requirement_inflation(
        modality="required",
        supporting_jd_count=8,
        required_supporting_jd_count=5,
        required_prevalence=0.25,
        required_purity=0.625,
        enterprise_count=5,
        gate=gate,
    )
    enterprise_specific = classify_requirement_inflation(
        modality="required",
        supporting_jd_count=3,
        required_supporting_jd_count=1,
        required_prevalence=0.05,
        required_purity=0.3333,
        enterprise_count=2,
        gate=gate,
    )
    inflation_risk = classify_requirement_inflation(
        modality="required",
        supporting_jd_count=1,
        required_supporting_jd_count=1,
        required_prevalence=0.02,
        required_purity=1.0,
        enterprise_count=1,
        gate=gate,
    )
    preferred = classify_requirement_inflation(
        modality="preferred",
        supporting_jd_count=1,
        required_supporting_jd_count=0,
        required_prevalence=0.0,
        required_purity=0.0,
        enterprise_count=1,
        gate=gate,
    )

    assert market.status == "market_supported"
    assert market.reason_codes == ()
    assert enterprise_specific.status == "enterprise_specific"
    assert "LOW_MARKET_REQUIRED_PREVALENCE" in enterprise_specific.reason_codes
    assert inflation_risk.status == "inflation_risk"
    assert inflation_risk.inflation_risk is True
    assert inflation_risk.reason_codes[-1] == (
        "INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT"
    )
    assert preferred.status == "not_applicable"
    assert [
        requirement_inflation_risk_level(value)
        for value in (0.2, 0.2001, 0.4, 0.4001)
    ] == ["low", "medium", "medium", "high"]


def test_threshold_change_changes_build_config_version():
    changed = PositionProfileThresholdConfig(
        skill_importance={
            "core": SkillImportanceThreshold(0.20, 3),
            "important": SkillImportanceThreshold(0.10, 2),
            "supplementary": SkillImportanceThreshold(0.05, 2),
        },
        responsibility={
            "core": ResponsibilityThreshold(0.15, 3),
            "important": ResponsibilityThreshold(0.10, 2),
            "supplementary": ResponsibilityThreshold(0.05, 2),
        },
        required_skill_gate=RequiredSkillGate(0.15, 3, 0.5),
    )
    assert build_config_version("algo-v1", 0.05, 1, changed) != build_config_version(
        "algo-v1", 0.05, 1, DEFAULT_POSITION_PROFILE_THRESHOLDS
    )


def test_partial_threshold_config_keeps_nested_defaults():
    merged = merged_relation_config(
        {
            "position_profile_thresholds": {
                "skill_importance": {
                    "core": {
                        "min_support_ratio": 0.20,
                        "min_supporting_jd_count": 3,
                    }
                }
            }
        }
    )
    parsed = PositionProfileThresholdConfig.from_serialized(
        merged["position_profile_thresholds"]
    )
    assert set(parsed.skill_importance) == {
        "core",
        "important",
        "supplementary",
    }
    assert parsed.skill_importance["core"].min_support_ratio == 0.20
    assert parsed.skill_importance["important"].min_support_ratio == 0.10
    assert build_config_version("algo-v1", 0.05, 1, parsed)


def test_build_config_version_stays_within_column_limit():
    version = build_config_version(
        "x" * 50,
        0.05,
        10**100,
        DEFAULT_POSITION_PROFILE_THRESHOLDS,
    )
    assert len(version) <= 128


def test_profile_filters_by_dedup_support_and_keeps_full_graph_evidence(
    client, db, auth_headers
):
    headers = auth_headers()
    version = _publish_profile(client, db, headers)
    profile = client.get(
        "/api/v1/position-profiles/BACKEND_ENGINEER",
        params={"contract_version": "position-profile.v3"},
        headers=headers,
    ).json()["data"]
    skills = {item["skill_id"]: item for item in profile["skill_relations"]}
    assert "SKILL_RARE" not in skills
    python = skills["SKILL_PYTHON"]
    assert python["modality"] == "required"
    assert python["supporting_jd_count"] == 3
    assert python["evidence_count"] == 4
    assert python["required_prevalence"] == 0.5
    assert python["required_purity"] == 0.6667
    assert python["profile_tier"] in {"market_core", "specialty", "observed"}
    assert skills["SKILL_DOCKER"]["modality"] == "preferred"
    assert skills["SKILL_DOCKER"]["importance_level"] in {
        "core",
        "important",
        "supplementary",
    }
    assert skills["SKILL_SQL"]["supporting_jd_count"] == 2
    assert python["requirement_market_status"] == "enterprise_specific"
    assert python["enterprise_count"] == 3
    assert skills["SKILL_DOCKER"]["requirement_market_status"] == "not_applicable"

    inflation = profile["requirement_inflation"]
    assert inflation["algorithm_version"] == "requirement-strength-calibration.v1"
    assert inflation["summary"] == {
        "jd_count": 3,
        "total_required_requirement_count": 5,
        "market_supported_count": 0,
        "enterprise_specific_count": 4,
        "inflation_risk_count": 1,
        "jd_risk_level_counts": {"low": 2, "medium": 1, "high": 0},
    }
    jd1 = next(
        item for item in inflation["jd_diagnostics"]
        if item["document_id"] == "JD1"
    )
    assert jd1["required_skill_count"] == 3
    assert jd1["inflation_risk_skill_count"] == 1
    assert jd1["inflation_ratio"] == 0.3333
    assert jd1["risk_level"] == "medium"
    rare = next(
        item for item in jd1["requirements"]
        if item["skill_id"] == "SKILL_RARE"
    )
    assert rare["market_status"] == "inflation_risk"
    assert rare["market"]["leave_one_out_required_jd_count"] == 0
    assert rare["market"]["leave_one_out_enterprise_count"] == 0

    responsibilities = {
        item.get("topic"): item
        for item in profile["responsibilities"]
    }
    assert responsibilities["系统与方案设计"]["importance_level"] == "core"
    assert responsibilities["系统与方案设计"]["supporting_jd_count"] == 3
    assert all(item["text"] != "服务开发" for item in profile["responsibilities"])
    assert all(item["text"] != "性能优化" for item in profile["responsibilities"])
    assert profile["quality"]["responsibility_filtered_by_level"]["supplementary"] == 2

    graph_skills = client.get(
        "/api/v1/positions/BACKEND_ENGINEER/graph",
        headers=headers,
    ).json()["data"]["skill_relations"]
    assert any(item["skill_id"] == "SKILL_RARE" for item in graph_skills)
    rare_supports = db.scalars(
        select(PositionSkillSupport).where(
            PositionSkillSupport.build_run_id == version.build_run_id,
            PositionSkillSupport.skill_id == "SKILL_RARE",
        )
    ).all()
    assert rare_supports
    assert all(
        item["skill_id"] != "SKILL_RARE"
        for item in profile["evidence_summary"]
    )

    thresholds = profile["dependencies"]["position_profile_thresholds"]
    assert thresholds["required_skill_gate"]["min_required_prevalence"] == 0.15
    assert ":build:" in profile["dependencies"]["build_config_version"]
    assert profile["dependencies"]["build_config_version"] == (
        version.build_config_version
    )


def test_duplicate_cluster_deduplicates_cross_jd_support(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    for doc_id in ("DUP_A", "DUP_B"):
        _prepare_profile_jd(
            client,
            headers,
            doc_id,
            skills=[("Python", "required")],
            responsibilities=[],
            source_name="board-a",
        )
    for doc_id, responsibility in (
        ("DUP_C", "部署服务"),
        ("DUP_D", "性能优化"),
    ):
        _prepare_profile_jd(
            client,
            headers,
            doc_id,
            skills=[("Python", "required")],
            responsibilities=[responsibility],
            source_name=f"board-{doc_id}",
        )
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={},
        headers=headers,
    ).json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    published = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={"reason": "duplicate cluster"},
        headers=headers,
    )
    assert published.status_code == 200

    shared_cluster = next(
        cluster
        for cluster in db.scalars(select(DuplicateCluster)).all()
        if {"DUP_A", "DUP_B"} <= set(cluster.document_ids)
    )
    assert shared_cluster.cluster_key.startswith("duplicate:")
    profile = client.get(
        "/api/v1/position-profiles/BACKEND_ENGINEER",
        params={"contract_version": "position-profile.v3"},
        headers=headers,
    ).json()["data"]
    python = next(
        item for item in profile["skill_relations"]
        if item["skill_id"] == "SKILL_PYTHON"
    )
    assert python["supporting_jd_count"] == 3
    assert python["evidence_count"] == 4
    duplicate_a = next(
        item for item in profile["requirement_inflation"]["jd_diagnostics"]
        if item["document_id"] == "DUP_A"
    )
    duplicate_a_python = duplicate_a["requirements"][0]
    assert duplicate_a_python["market"]["leave_one_out_required_jd_count"] == 2
    assert duplicate_a_python["market"]["leave_one_out_enterprise_count"] == 2
    assert duplicate_a_python["market"]["leave_one_out_source_count"] == 2


def test_rollback_profile_keeps_support_statistics(client, db, auth_headers):
    headers = auth_headers()
    first = _publish_profile(client, db, headers)
    second_build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={},
        headers=headers,
    ).json()["data"]
    approve_build_tasks(client, second_build["build_run_id"], headers)
    second = client.post(
        f'/api/v1/graph/build-runs/{second_build["build_run_id"]}/publish',
        json={"reason": "second version"},
        headers=headers,
    ).json()["data"]
    db.get(GraphVersion, second["version_id"])

    rollback = client.post(
        f"/api/v1/positions/BACKEND_ENGINEER/graph/versions/{first.id}/rollback",
        json={"reason": "restore first"},
        headers=headers,
    )
    assert rollback.status_code == 200, rollback.text
    profile = client.get(
        "/api/v1/position-profiles/BACKEND_ENGINEER",
        params={"contract_version": "position-profile.v3"},
        headers=headers,
    ).json()["data"]
    skills = {item["skill_id"]: item for item in profile["skill_relations"]}
    assert skills["SKILL_PYTHON"]["supporting_jd_count"] == 3
    assert skills["SKILL_PYTHON"]["evidence_count"] == 4


def test_published_profile_ignores_later_cluster_changes(client, db, auth_headers):
    headers = auth_headers()
    version = _publish_profile(client, db, headers)

    def python_support(profile):
        return next(
            item for item in profile["skill_relations"]
            if item["skill_id"] == "SKILL_PYTHON"
        )

    before = python_support(
        client.get(
            "/api/v1/position-profiles/BACKEND_ENGINEER",
            params={"contract_version": "position-profile.v3"},
            headers=headers,
        ).json()["data"]
    )
    db.add(
        DuplicateCluster(
            cluster_key="late-cluster",
            document_ids=["JD1", "JD2"],
            score=1.0,
        )
    )
    db.commit()
    after = python_support(
        client.get(
            "/api/v1/position-profiles/BACKEND_ENGINEER",
            params={
                "contract_version": "position-profile.v3",
                "graph_version_id": version.id,
            },
            headers=headers,
        ).json()["data"]
    )
    assert after["supporting_jd_count"] == before["supporting_jd_count"] == 3
    assert after["evidence_count"] == before["evidence_count"] == 4


def test_responsibility_topic_normalization_merges_synonyms():
    assert normalize_responsibility_topic("负责系统架构设计与方案评审") == "系统与方案设计"
    merged = merge_responsibility_topics(
        (
            {
                "text": "负责系统架构设计与方案评审",
                "document_ids": ["JD1"],
                "evidence_ids": [1],
            },
            {
                "text": "参与系统设计并输出设计文档",
                "document_ids": ["JD2"],
                "evidence_ids": [2],
            },
        )
    )
    assert len(merged) == 1
    assert merged[0]["topic"] == "系统与方案设计"
    assert merged[0]["text"] in {
        "负责系统架构设计与方案评审",
        "参与系统设计并输出设计文档",
    }
    assert merged[0]["representative_text"] == merged[0]["text"]
    assert merged[0]["source_texts"] == [
        "负责系统架构设计与方案评审",
        "参与系统设计并输出设计文档",
    ]
    assert merged[0]["document_ids"] == ["JD1", "JD2"]


def test_responsibility_topics_merge_across_jd_synonyms(client, db, auth_headers):
    headers = auth_headers()
    prepare_catalog(db)
    _prepare_profile_jd(
        client,
        headers,
        "SYN_A",
        skills=[("Python", "required")],
        responsibilities=["负责系统架构设计与方案评审"],
        source_name="board-a",
    )
    _prepare_profile_jd(
        client,
        headers,
        "SYN_B",
        skills=[("Python", "required")],
        responsibilities=["参与系统设计并输出设计文档"],
        source_name="board-b",
    )
    _prepare_profile_jd(
        client,
        headers,
        "SYN_C",
        skills=[("Python", "required")],
        responsibilities=[],
        source_name="board-c",
    )
    build = client.post(
        "/api/v1/positions/BACKEND_ENGINEER/graph/build",
        json={},
        headers=headers,
    ).json()["data"]
    approve_build_tasks(client, build["build_run_id"], headers)
    published = client.post(
        f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',
        json={"reason": "responsibility topics"},
        headers=headers,
    )
    assert published.status_code == 200
    profile = client.get(
        "/api/v1/position-profiles/BACKEND_ENGINEER",
        params={"contract_version": "position-profile.v3"},
        headers=headers,
    ).json()["data"]
    responsibilities = [
        item
        for item in profile["responsibilities"]
        if item.get("topic") == "系统与方案设计"
    ]
    assert len(responsibilities) == 1
    assert responsibilities[0]["text"] not in {"系统设计", "系统与方案设计"}
    assert responsibilities[0]["supporting_jd_count"] == 2
