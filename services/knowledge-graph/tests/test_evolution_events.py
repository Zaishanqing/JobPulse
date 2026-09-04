from __future__ import annotations

from app.domain.evolution import (
    EVIDENCE_QUALITY_UNAVAILABLE,
    _evidence_quality,
    _evidence_quality_details,
    _read_relation_metric,
    detect_evolution_events,
)
from app.models import GraphBuildRun, GraphVersion, StandardPosition


POSITION_ID = "POS_EVOLUTION"


def _relation(
    skill_id: str,
    name: str,
    weight: float,
    category: str,
    *,
    confidence: float = 0.9,
    classifications: tuple = (),
) -> dict:
    return {
        "skill_id": skill_id,
        "canonical_name": name,
        "category_code": category,
        "subcategory_code": None,
        "classifications": list(classifications),
        "taxonomy_version": "test-taxonomy",
        "auto_weight": weight,
        "final_weight": weight,
        "weight": weight,
        "auto_confidence": confidence,
        "final_confidence": confidence,
        "confidence": confidence,
        "importance_level": "important" if weight >= 0.4 else "common",
        "primary_modality": "required",
        "statistics": {
            "support_document_count": 3,
            "source_diversity": 2,
            "enterprise_coverage": 2,
        },
        "explanation": {"method": "test"},
    }


def _snapshot(
    skills: list[dict],
    *,
    responsibilities: tuple[str, ...] = (),
    name: str = "AI Engineer",
) -> dict:
    return {
        "position_id": POSITION_ID,
        "position": {
            "position_id": POSITION_ID,
            "name": name,
            "category_code": "TECH",
        },
        "time_window": {
            "start": "2026-01-01",
            "end": "2026-03-31",
        },
        "sample_stats": {"included_samples": 3},
        "skill_relations": skills,
        "responsibilities": [{"text": value} for value in responsibilities],
        "requirement_profile": [],
    }


def _create_versions(db, before: dict, after: dict) -> tuple[int, int]:
    position = StandardPosition(
        position_id=POSITION_ID,
        name=str(before["position"]["name"]),
        category_code="TECH",
    )
    db.add(position)
    db.flush()

    run_before = GraphBuildRun(
        position_id=POSITION_ID,
        status="succeeded",
        config_snapshot={},
    )
    db.add(run_before)
    db.flush()
    version_before = GraphVersion(
        position_id=POSITION_ID,
        build_run_id=run_before.id,
        version_number=1,
        version_name="v1",
        snapshot=before,
        source_version="v1",
        algorithm_version="test",
        normalization_map_version="test",
    )
    db.add(version_before)
    db.flush()

    run_after = GraphBuildRun(
        position_id=POSITION_ID,
        status="succeeded",
        config_snapshot={},
    )
    db.add(run_after)
    db.flush()
    version_after = GraphVersion(
        position_id=POSITION_ID,
        build_run_id=run_after.id,
        version_number=2,
        version_name="v2",
        snapshot=after,
        source_version="v2",
        algorithm_version="test",
        normalization_map_version="test",
    )
    db.add(version_after)
    db.flush()
    position.current_version_id = version_after.id
    db.commit()
    return version_before.id, version_after.id


def test_new_skill_creates_skill_emergence_event():
    before = _snapshot([_relation("PY", "Python", 0.4, "LANG")])
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
        ]
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    emergence = next(
        item
        for item in events
        if item["event_type"] == "skill_emergence"
        and item["target_entities"][0]["skill_id"] == "RAG"
    )
    assert emergence["magnitude"] > 0
    assert emergence["evidence"]["lineage"]["from_version_id"] == 1
    assert emergence["evidence"]["lineage"]["to_version_id"] == 2


def test_skill_decline_event_is_detected():
    before = _snapshot([_relation("TF", "TensorFlow", 0.6, "ML")])
    after = _snapshot([_relation("TF", "TensorFlow", 0.2, "ML")])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    decline = next(
        item
        for item in events
        if item["event_type"] == "skill_decline"
        and item["source_entities"][0]["skill_id"] == "TF"
    )
    assert decline["metrics"]["delta"] == -0.4


def test_related_skill_replacement_is_detected():
    before = _snapshot([_relation("TF", "TensorFlow", 0.6, "ML")])
    after = _snapshot(
        [
            _relation("TF", "TensorFlow", 0.2, "ML"),
            _relation("PT", "PyTorch", 0.5, "ML"),
        ]
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    replacement = next(
        item
        for item in events
        if item["event_type"] == "skill_replacement"
    )
    assert replacement["source_entities"][0]["skill_id"] == "TF"
    assert replacement["target_entities"][0]["skill_id"] == "PT"


def test_unrelated_skill_change_is_not_replacement():
    before = _snapshot([_relation("EXCEL", "Excel", 0.5, "OFFICE")])
    after = _snapshot([_relation("K8S", "Kubernetes", 0.5, "INFRA")])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_replacement" not in {item["event_type"] for item in events}


def test_multiple_related_replacements_create_stack_migration():
    before = _snapshot(
        [
            _relation("TF", "TensorFlow", 0.6, "ML"),
            _relation("KERAS", "Keras", 0.5, "ML"),
        ]
    )
    after = _snapshot(
        [
            _relation("TF", "TensorFlow", 0.2, "ML"),
            _relation("KERAS", "Keras", 0.15, "ML"),
            _relation("PT", "PyTorch", 0.5, "ML"),
            _relation("TRANSFORMERS", "Transformers", 0.4, "ML"),
            _relation("PEFT", "PEFT", 0.3, "ML"),
        ]
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    types = {item["event_type"] for item in events}
    assert "skill_replacement" in types
    assert "technology_stack_migration" in types
    stack = next(
        item
        for item in events
        if item["event_type"] == "technology_stack_migration"
    )
    assert {"TF", "KERAS"} <= {
        item["skill_id"] for item in stack["source_entities"]
    }
    assert {"PT", "TRANSFORMERS", "PEFT"} <= {
        item["skill_id"] for item in stack["target_entities"]
    }


def test_responsibility_shift_event():
    before = _snapshot(
        [],
        responsibilities=("模型训练", "数据处理", "部署"),
    )
    after = _snapshot(
        [],
        responsibilities=("模型训练", "RAG 系统设计", "评测", "检索"),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    shift = next(
        item
        for item in events
        if item["event_type"] == "responsibility_shift"
    )
    assert shift["metrics"]["removed_count"] == 2
    assert shift["metrics"]["added_count"] == 3


def test_role_expansion_event():
    before = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("应用开发",),
    )
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
            _relation("DEPLOY", "Deploy", 0.3, "OPS"),
        ],
        responsibilities=("应用开发", "评测", "部署"),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "role_expansion" in {item["event_type"] for item in events}


def test_role_contraction_event():
    before = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
            _relation("DEPLOY", "Deploy", 0.3, "OPS"),
        ],
        responsibilities=("应用开发", "评测", "部署"),
    )
    after = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("应用开发",),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "role_contraction" in {item["event_type"] for item in events}


def test_small_noise_changes_produce_no_events():
    before = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("负责开发",),
    )
    after = _snapshot(
        [_relation("PY", "Python", 0.42, "LANG")],
        responsibilities=("负责开发",),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert events == []


def test_events_and_confidence_are_deterministic():
    before = _snapshot(
        [_relation("TF", "TensorFlow", 0.6, "ML")],
        responsibilities=("模型训练",),
    )
    after = _snapshot(
        [
            _relation("TF", "TensorFlow", 0.2, "ML"),
            _relation("PT", "PyTorch", 0.5, "ML"),
        ],
        responsibilities=("模型训练", "部署"),
    )
    first = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    second = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert first == second
    assert [item["confidence"] for item in first] == [
        item["confidence"] for item in second
    ]
    assert all(0.0 <= item["confidence"] <= 1.0 for item in first)


def test_evidence_and_version_lineage_are_preserved():
    before = _snapshot([_relation("PY", "Python", 0.4, "LANG")])
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
        ]
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    for event in events:
        assert event["evidence"]["lineage"]["position_id"] == POSITION_ID
        assert event["evidence"]["lineage"]["from_version_id"] == 1
        assert event["evidence"]["lineage"]["to_version_id"] == 2
        assert event["reason"]
        assert event["detector_version"]
        assert event["config_version"] == "evolution-defaults-v1"
        assert event["from_graph_version_id"] == 1
        assert event["to_graph_version_id"] == 2


def test_same_input_does_not_produce_duplicate_events():
    before = _snapshot(
        [_relation("TF", "TensorFlow", 0.6, "ML")],
        responsibilities=("模型训练",),
    )
    after = _snapshot(
        [
            _relation("TF", "TensorFlow", 0.2, "ML"),
            _relation("PT", "PyTorch", 0.5, "ML"),
        ],
        responsibilities=("模型训练", "部署"),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    ids = [item["event_id"] for item in events]
    assert len(ids) == len(set(ids))


def test_api_lists_filters_and_returns_event_detail(db, client):
    before = _snapshot([_relation("PY", "Python", 0.4, "LANG")])
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
        ]
    )
    before_id, after_id = _create_versions(db, before, after)
    response = client.get(
        f"/api/v1/positions/{POSITION_ID}/graph/versions/evolution-events"
        f"?from_version_id={before_id}&to_version_id={after_id}"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] > 0
    event = data["events"][0]
    detail = client.get(
        f"/api/v1/positions/{POSITION_ID}/graph/versions/evolution-events/"
        f"{event['event_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["data"] == event

    filtered = client.get(
        f"/api/v1/positions/{POSITION_ID}/graph/versions/evolution-events"
        f"?from_version_id={before_id}&to_version_id={after_id}"
        "&event_type=skill_emergence"
    ).json()["data"]
    assert filtered["count"] >= 1
    assert all(
        item["event_type"] == "skill_emergence" for item in filtered["events"]
    )


# -- Evidence Quality (A4) --


def test_read_relation_metric_preserves_zero_and_distinguishes_missing():
    assert _read_relation_metric(
        {"statistics": {"support_document_count": 0}},
        "support_document_count",
    ) == 0
    assert _read_relation_metric(
        {"statistics": {}},
        "support_document_count",
    ) is None
    assert _read_relation_metric(
        {"metrics": {"support_document_count": 3}},
        "support_document_count",
    ) == 3


def test_evidence_quality_reads_from_metrics_field():
    relation_with_metrics = {
        "skill_id": "SK1",
        "canonical_name": "Test Skill",
        "auto_weight": 0.5,
        "auto_confidence": 0.8,
        "metrics": {
            "support_document_count": 5,
            "source_diversity": 3,
            "enterprise_coverage": 3,
        },
    }
    quality = _evidence_quality(relation_with_metrics)
    assert quality > 0.7
    assert quality > EVIDENCE_QUALITY_UNAVAILABLE


def test_evidence_quality_reads_from_statistics_field():
    relation_with_stats = {
        "skill_id": "SK1",
        "auto_weight": 0.5,
        "auto_confidence": 0.8,
        "statistics": {
            "support_document_count": 5,
            "source_diversity": 3,
            "enterprise_coverage": 3,
        },
    }
    quality = _evidence_quality(relation_with_stats)
    assert quality > 0.7


def test_evidence_quality_falls_back_to_unavailable_when_no_metadata():
    relation_no_metadata = {
        "skill_id": "SK1",
        "auto_weight": 0.5,
        "auto_confidence": 0.8,
    }
    quality = _evidence_quality(relation_no_metadata)
    assert quality == EVIDENCE_QUALITY_UNAVAILABLE


def test_evidence_quality_prefers_statistics_over_metrics():
    relation_both = {
        "skill_id": "SK1",
        "statistics": {
            "support_document_count": 10,
            "source_diversity": 6,
            "enterprise_coverage": 6,
        },
        "metrics": {
            "support_document_count": 1,
            "source_diversity": 1,
            "enterprise_coverage": 1,
        },
    }
    quality = _evidence_quality(relation_both)
    assert quality > 0.8


def test_low_support_vs_high_support_have_real_quality_difference():
    low_support = {
        "skill_id": "LOW",
        "statistics": {
            "support_document_count": 1,
            "source_diversity": 1,
            "enterprise_coverage": 1,
        },
    }
    high_support = {
        "skill_id": "HIGH",
        "statistics": {
            "support_document_count": 10,
            "source_diversity": 6,
            "enterprise_coverage": 6,
        },
    }
    low_quality = _evidence_quality(low_support)
    high_quality = _evidence_quality(high_support)
    assert high_quality > low_quality
    assert high_quality - low_quality > 0.15


def test_evidence_quality_details_includes_breakdown():
    relation = {
        "skill_id": "SK1",
        "statistics": {
            "support_document_count": 4,
            "source_diversity": 2,
            "enterprise_coverage": 3,
        },
    }
    details = _evidence_quality_details(relation)
    assert details["available"] is True
    assert details["quality"] > EVIDENCE_QUALITY_UNAVAILABLE
    components = details["components"]
    assert components["support_document_count"] == 4.0
    assert components["source_diversity"] == 2.0
    assert components["enterprise_coverage"] == 3.0
    assert "computed from" in str(details["reason"])


def test_evidence_quality_details_reports_unavailable():
    relation = {"skill_id": "SK1"}
    details = _evidence_quality_details(relation)
    assert details["available"] is False
    assert details["quality"] == EVIDENCE_QUALITY_UNAVAILABLE
    assert "EVIDENCE_QUALITY_UNAVAILABLE" in str(details["reason"])


def test_event_metrics_include_evidence_quality_details():
    before = _snapshot([])
    after = _snapshot(
        [_relation("RUST", "Rust", 0.5, "LANG")]
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    emergence = next(
        item for item in events if item["event_type"] == "skill_emergence"
    )
    eq_info = emergence["metrics"]["evidence_quality"]
    assert eq_info["available"] is True
    assert eq_info["quality"] > 0.5


def test_confidence_drops_when_evidence_quality_is_unavailable():
    before = _snapshot([])
    no_evidence_skill = {
        "skill_id": "GO",
        "canonical_name": "Go",
        "auto_weight": 0.5,
        "final_weight": 0.5,
        "auto_confidence": 0.8,
        "final_confidence": 0.8,
        "category_code": "LANG",
        "importance_level": "important",
    }
    after_no_evidence = _snapshot([no_evidence_skill])
    events_unavailable = detect_evolution_events(
        before, after_no_evidence, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    emergence_unavailable = next(
        item for item in events_unavailable if item["event_type"] == "skill_emergence"
    )

    with_evidence_skill = _relation("GO", "Go", 0.5, "LANG")
    after_with_evidence = _snapshot([with_evidence_skill])
    events_with = detect_evolution_events(
        before, after_with_evidence, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    emergence_with = next(
        item for item in events_with if item["event_type"] == "skill_emergence"
    )

    eq_unavailable = emergence_unavailable["metrics"]["evidence_quality"]
    eq_with = emergence_with["metrics"]["evidence_quality"]
    assert eq_unavailable["available"] is False
    assert eq_unavailable["quality"] == EVIDENCE_QUALITY_UNAVAILABLE
    assert eq_with["available"] is True
    assert eq_with["quality"] > eq_unavailable["quality"]
    assert emergence_with["confidence"] > emergence_unavailable["confidence"]


def test_event_exposes_detector_and_config_identity():
    before = _snapshot([_relation("PY", "Python", 0.4, "LANG")])
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
        ]
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    for event in events:
        assert event["detector_version"] == "position-evolution-events-v5"
        assert event["config_version"] == "evolution-defaults-v1"
        assert event["from_graph_version_id"] == 1
        assert event["to_graph_version_id"] == 2


def test_same_versions_same_config_are_reproducible():
    before = _snapshot(
        [_relation("TF", "TensorFlow", 0.6, "ML")],
        responsibilities=("模型训练",),
    )
    after = _snapshot(
        [
            _relation("TF", "TensorFlow", 0.2, "ML"),
            _relation("PT", "PyTorch", 0.5, "ML"),
        ],
        responsibilities=("模型训练", "部署"),
    )
    first = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    second = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert first == second
    for event in first:
        assert event["config_version"] == "evolution-defaults-v1"
        assert event["detector_version"] == "position-evolution-events-v5"


def test_config_override_changes_derived_view_identity():
    before = _snapshot([_relation("PY", "Python", 0.4, "LANG")])
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
        ]
    )
    custom_config = {
        "config_version": "custom-experiment-v2",
        "detector_version": "custom-detector-v2",
        "emergence_min_absolute_weight": 0.30,
    }
    events = detect_evolution_events(
        before, after,
        position_id=POSITION_ID, from_version_id=1, to_version_id=2,
        config=custom_config,
    )
    for event in events:
        assert event["config_version"] == "custom-experiment-v2"
        assert event["detector_version"] == "custom-detector-v2"


def test_config_version_field_present_on_all_event_types():
    before = _snapshot(
        [_relation("TF", "TensorFlow", 0.6, "ML")],
        responsibilities=("模型训练",),
    )
    after = _snapshot(
        [
            _relation("TF", "TensorFlow", 0.2, "ML"),
            _relation("PT", "PyTorch", 0.5, "ML"),
        ],
        responsibilities=("模型训练", "部署"),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    event_types_found = {item["event_type"] for item in events}
    for event in events:
        assert event["config_version"] == "evolution-defaults-v1"
        assert event["detector_version"] == "position-evolution-events-v5"
        assert isinstance(event["from_graph_version_id"], int)
        assert isinstance(event["to_graph_version_id"], int)
    assert "skill_decline" in event_types_found
    assert "skill_emergence" in event_types_found
    assert "skill_replacement" in event_types_found


# -- position_split / position_merge (A7) --


def test_position_split_when_coherent_cluster_of_skills_disappears():
    """同一类别大量技能消失 + 职责减少 → position_split"""
    before = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
            _relation("NLP", "NLP", 0.45, "AI"),
            _relation("CV", "Computer Vision", 0.4, "AI"),
            _relation("TRANSFORMERS", "Transformers", 0.35, "AI"),
        ],
        responsibilities=("模型训练", "数据处理", "RAG 系统设计"),
    )
    after = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("应用开发",),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    types = {item["event_type"] for item in events}
    assert "position_split" in types


def test_no_split_when_skills_disappear_but_not_coherent():
    """零星技能消失但不构成类别集群 → 不触发 split"""
    before = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("DOCKER", "Docker", 0.3, "INFRA"),
            _relation("EXCEL", "Excel", 0.2, "OFFICE"),
        ],
        responsibilities=("开发", "运维"),
    )
    after = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("开发",),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "position_split" not in {item["event_type"] for item in events}


def test_position_merge_when_coherent_cluster_of_new_skills_appears():
    """新领域大量技能出现 + 职责增加 → position_merge"""
    before = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("应用开发",),
    )
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
            _relation("NLP", "NLP", 0.45, "AI"),
            _relation("CV", "Computer Vision", 0.4, "AI"),
            _relation("TRANSFORMERS", "Transformers", 0.35, "AI"),
        ],
        responsibilities=("应用开发", "模型训练", "数据处理", "RAG 系统设计"),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    types = {item["event_type"] for item in events}
    assert "position_merge" in types


def test_no_merge_when_new_skills_already_in_existing_category():
    """新技能属于已有类别 → 不触发 merge（只是 expansion）"""
    before = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("应用开发",),
    )
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("GO", "Go", 0.5, "LANG"),
            _relation("TS", "TypeScript", 0.45, "LANG"),
            _relation("RS", "Rust", 0.4, "LANG"),
        ],
        responsibilities=("应用开发", "性能优化"),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    types = {item["event_type"] for item in events}
    assert "position_merge" not in types
    # 应该是 expansion 而非 merge
    assert "role_expansion" in types


def test_split_event_has_correct_metrics():
    """验证 split event 的 metrics 字段"""
    before = _snapshot(
        [
            _relation("RAG", "RAG", 0.5, "AI"),
            _relation("NLP", "NLP", 0.45, "AI"),
            _relation("TRANSFORMERS", "Transformers", 0.35, "AI"),
            _relation("PY", "Python", 0.4, "LANG"),
        ],
        responsibilities=("模型训练", "数据处理", "RAG 设计"),
    )
    after = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("应用开发",),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    split = next(
        item for item in events if item["event_type"] == "position_split"
    )
    m = split["metrics"]
    assert m["removed_skill_count"] == 3
    assert m["cluster_skill_count"] == 3
    assert m["cluster_category"] == "AI"
    assert m["removed_responsibility_count"] >= 2
    assert m["skill_fraction_removed"] >= 0.25
    assert split["detector_version"]
    assert split["config_version"]


def test_split_and_merge_events_have_version_identity():
    """split/merge 事件包含 detector/config version"""
    before = _snapshot(
        [
            _relation("RAG", "RAG", 0.5, "AI"),
            _relation("NLP", "NLP", 0.45, "AI"),
            _relation("TRANSFORMERS", "Transformers", 0.35, "AI"),
            _relation("PY", "Python", 0.4, "LANG"),
        ],
        responsibilities=("模型训练", "数据处理", "RAG 设计"),
    )
    after = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("应用开发",),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    split_merge = [
        item for item in events
        if item["event_type"] in ("position_split", "position_merge")
    ]
    for event in split_merge:
        assert event["detector_version"] == "position-evolution-events-v5"
        assert event["config_version"] == "evolution-defaults-v1"
        assert event["from_graph_version_id"] == 1
        assert event["to_graph_version_id"] == 2


# -- A-BUG-02: confidence_available / confidence_source --


def test_skill_event_has_confidence_available_true_with_real_evidence():
    """Events from relations with real statistics/metrics have confidence_available=True."""
    before = _snapshot([])
    after = _snapshot([_relation("RAG", "RAG", 0.5, "AI")])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    emergence = next(e for e in events if e["event_type"] == "skill_emergence")
    assert emergence["confidence_available"] is True
    assert emergence["confidence_source"] == "computed"


def test_skill_event_has_confidence_available_false_when_evidence_missing():
    """Events from relations with no statistics/metrics have confidence_available=False."""
    before = _snapshot([])
    no_evidence_skill = {
        "skill_id": "GO",
        "canonical_name": "Go",
        "auto_weight": 0.5,
        "final_weight": 0.5,
        "auto_confidence": 0.8,
        "final_confidence": 0.8,
        "category_code": "LANG",
        "importance_level": "important",
    }
    after = _snapshot([no_evidence_skill])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    emergence = next(e for e in events if e["event_type"] == "skill_emergence")
    assert emergence["confidence_available"] is False
    assert "EVIDENCE_QUALITY_UNAVAILABLE" in emergence["confidence_source"]


def test_skill_event_confidence_unavailable_when_no_confidence_field():
    """Events from relations without any confidence field get CONFIDENCE_UNAVAILABLE."""
    before = _snapshot([])
    no_confidence_skill = {
        "skill_id": "RUST",
        "canonical_name": "Rust",
        "auto_weight": 0.5,
        "final_weight": 0.5,
        "category_code": "LANG",
        "importance_level": "important",
        "statistics": {
            "support_document_count": 5,
            "source_diversity": 3,
            "enterprise_coverage": 3,
        },
    }
    after = _snapshot([no_confidence_skill])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    emergence = next(e for e in events if e["event_type"] == "skill_emergence")
    assert emergence["confidence_available"] is False
    assert "CONFIDENCE_UNAVAILABLE" in emergence["confidence_source"]


def test_position_rename_has_directly_observed_confidence_source():
    before = _snapshot([_relation("PY", "Python", 0.4, "LANG")], name="Old Name")
    after = _snapshot([_relation("PY", "Python", 0.4, "LANG")], name="New Name")
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    rename = next(e for e in events if e["event_type"] == "position_rename")
    assert rename["confidence_available"] is True
    assert rename["confidence_source"] == "directly_observed"


def test_responsibility_shift_has_fixed_estimate_confidence_source():
    before = _snapshot([], responsibilities=("任务A", "任务B", "任务C"))
    after = _snapshot([], responsibilities=("任务C", "任务D", "任务E"))
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    shift = next(e for e in events if e["event_type"] == "responsibility_shift")
    assert shift["confidence_available"] is True
    assert shift["confidence_source"] == "fixed_estimate"


def test_no_overlap_responsibility_does_not_fire_shift():
    """职责文本完全无重叠（抽样换血）时不应判为职责迁移。"""
    before = _snapshot([], responsibilities=("任务A", "任务B"))
    after = _snapshot([], responsibilities=("任务C", "任务D", "任务E"))
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "responsibility_shift" not in {e["event_type"] for e in events}


def test_one_sided_responsibility_change_does_not_fire_shift():
    """单边变化（仅增长无移除）不应判为职责迁移。"""
    before = _snapshot([], responsibilities=("任务A", "任务B"))
    after = _snapshot([], responsibilities=("任务A", "任务B", "任务C", "任务D"))
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "responsibility_shift" not in {e["event_type"] for e in events}


def test_role_expansion_has_fixed_estimate_confidence_source():
    before = _snapshot(
        [_relation("PY", "Python", 0.4, "LANG")],
        responsibilities=("开发",),
    )
    after = _snapshot(
        [
            _relation("PY", "Python", 0.4, "LANG"),
            _relation("RAG", "RAG", 0.5, "AI"),
            _relation("DEPLOY", "Deploy", 0.3, "OPS"),
        ],
        responsibilities=("开发", "评测", "部署"),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    expansion = next(e for e in events if e["event_type"] == "role_expansion")
    assert expansion["confidence_available"] is True
    assert expansion["confidence_source"] == "fixed_estimate"


def test_all_event_types_have_confidence_fields():
    """Every event must include confidence_available and confidence_source."""
    before = _snapshot(
        [_relation("TF", "TensorFlow", 0.6, "ML"), _relation("KERAS", "Keras", 0.5, "ML")],
        responsibilities=("训练", "评估"),
    )
    after = _snapshot(
        [
            _relation("TF", "TensorFlow", 0.2, "ML"),
            _relation("KERAS", "Keras", 0.15, "ML"),
            _relation("PT", "PyTorch", 0.5, "ML"),
            _relation("TRANSFORMERS", "Transformers", 0.4, "ML"),
        ],
        responsibilities=("训练", "部署"),
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    for event in events:
        assert "confidence_available" in event, (
            f"Event {event['event_id']} ({event['event_type']}) missing confidence_available"
        )
        assert "confidence_source" in event, (
            f"Event {event['event_id']} ({event['event_type']}) missing confidence_source"
        )
        assert isinstance(event["confidence_available"], bool)
        assert isinstance(event["confidence_source"], str)
        assert len(event["confidence_source"]) > 0


def test_both_unavailable_produces_combined_source():
    """When both evidence and confidence are unavailable, source contains both reasons."""
    before = _snapshot([])
    no_both_skill = {
        "skill_id": "BOTH",
        "canonical_name": "Both Missing",
        "auto_weight": 0.5,
        "category_code": "LANG",
    }
    after = _snapshot([no_both_skill])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    emergence = next(e for e in events if e["event_type"] == "skill_emergence")
    assert emergence["confidence_available"] is False
    assert "EVIDENCE_QUALITY_UNAVAILABLE" in emergence["confidence_source"]
    assert "CONFIDENCE_UNAVAILABLE" in emergence["confidence_source"]


def test_replacement_confidence_available_aggregates_both_signals():
    """Replacement confidence_available=False if either old or new signal lacks evidence."""
    old_no_evidence = {
        "skill_id": "TF",
        "canonical_name": "TensorFlow",
        "auto_weight": 0.6,
        "final_weight": 0.6,
        "auto_confidence": 0.8,
        "final_confidence": 0.8,
        "category_code": "ML",
        "importance_level": "important",
    }
    new_with_evidence = _relation("PT", "PyTorch", 0.5, "ML")
    before = _snapshot([old_no_evidence])
    after = _snapshot([old_no_evidence, new_with_evidence])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    replacement = next((e for e in events if e["event_type"] == "skill_replacement"), None)
    if replacement is not None:
        assert replacement["confidence_available"] is False
        assert "EVIDENCE_QUALITY_UNAVAILABLE" in replacement["confidence_source"]


# ── A18: coverage_artifact tests ──


def _relation_with_stats(
    skill_id: str,
    name: str,
    weight: float,
    category: str,
    *,
    confidence: float = 0.9,
    support_docs: int = 3,
    source_div: int = 2,
    enterprise_cov: int = 2,
) -> dict:
    r = _relation(skill_id, name, weight, category, confidence=confidence)
    r["statistics"] = {
        "support_document_count": support_docs,
        "source_diversity": source_div,
        "enterprise_coverage": enterprise_cov,
    }
    return r


def test_coverage_artifact_detected_when_2_of_3_coverage_metrics_shift():
    """Weight change + significant source & enterprise deltas → coverage_artifact."""
    before = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=2, enterprise_cov=2),
    ])
    after = _snapshot([
        _relation_with_stats("PY", "Python", 0.55, "LANG",
                             support_docs=8, source_div=4, enterprise_cov=5),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    ca_events = [e for e in events if e["event_type"] == "coverage_artifact"]
    assert len(ca_events) == 1
    ca = ca_events[0]
    assert ca["magnitude"] > 0
    cov_deltas = ca["metadata"]["coverage_deltas"]
    assert cov_deltas["source_diversity_delta"] >= 1
    assert cov_deltas["enterprise_coverage_delta"] >= 1
    assert cov_deltas["coverage_change_count"] >= 2


def test_coverage_artifact_not_fired_when_only_weight_changes():
    """No significant coverage metric changes → no coverage_artifact."""
    before = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=3, enterprise_cov=3),
    ])
    after = _snapshot([
        _relation_with_stats("PY", "Python", 0.6, "LANG",
                             support_docs=5, source_div=3, enterprise_cov=3),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    ca_events = [e for e in events if e["event_type"] == "coverage_artifact"]
    assert len(ca_events) == 0


def test_coverage_artifact_not_fired_when_weight_delta_below_threshold():
    """Small weight change → not flagged as coverage_artifact."""
    before = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=3, source_div=2, enterprise_cov=2),
    ])
    after = _snapshot([
        _relation_with_stats("PY", "Python", 0.42, "LANG",
                             support_docs=10, source_div=6, enterprise_cov=6),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    ca_events = [e for e in events if e["event_type"] == "coverage_artifact"]
    assert len(ca_events) == 0


def test_coverage_artifact_has_confidence_fields():
    """coverage_artifact event includes confidence_available and confidence_source."""
    before = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=2, enterprise_cov=2),
    ])
    after = _snapshot([
        _relation_with_stats("PY", "Python", 0.55, "LANG",
                             support_docs=9, source_div=5, enterprise_cov=5),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    ca = next((e for e in events if e["event_type"] == "coverage_artifact"), None)
    assert ca is not None
    assert ca["confidence_available"] is True
    assert ca["confidence_source"] == "computed"


def test_coverage_artifact_coexists_with_other_event_types():
    """coverage_artifact can appear alongside skill_emergence etc."""
    before = _snapshot([
        _relation_with_stats("JAVA", "Java", 0.5, "LANG",
                             support_docs=5, source_div=2, enterprise_cov=2),
    ])
    after = _snapshot([
        _relation_with_stats("JAVA", "Java", 0.65, "LANG",
                             support_docs=10, source_div=5, enterprise_cov=5),
        _relation("PY", "Python", 0.3, "LANG"),  # new skill
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    event_types = {e["event_type"] for e in events}
    assert "coverage_artifact" in event_types
    assert "skill_emergence" in event_types  # PY is new


def test_coverage_artifact_with_support_and_enterprise_delta():
    """When only support and enterprise change, still fires coverage_artifact."""
    before = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=3, enterprise_cov=2),
    ])
    after = _snapshot([
        _relation_with_stats("PY", "Python", 0.55, "LANG",
                             support_docs=12, source_div=3, enterprise_cov=6),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    ca_events = [e for e in events if e["event_type"] == "coverage_artifact"]
    assert len(ca_events) == 1
    cov = ca_events[0]["metadata"]["coverage_deltas"]
    assert cov["support_document_delta"] >= 3
    assert cov["enterprise_coverage_delta"] >= 1
    assert cov["coverage_change_count"] >= 2


# ———— A19: seniority_inflation ————


def _snapshot_with_requirements(
    skills: list[dict],
    *,
    requirement_profile: list[dict] | None = None,
    name: str = "AI Engineer",
) -> dict:
    """创建带 requirement_profile 的快照。"""
    return {
        "position_id": POSITION_ID,
        "position": {
            "position_id": POSITION_ID,
            "name": name,
            "category_code": "TECH",
        },
        "time_window": {"start": "2026-01-01", "end": "2026-03-31"},
        "sample_stats": {"included_samples": 3},
        "skill_relations": skills,
        "responsibilities": [],
        "requirement_profile": requirement_profile or [],
    }


def test_seniority_inflation_by_matching_requirement_id():
    """经验年限要求在同一 requirement_id 上下降 >=1 年时触发。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_exp_01",
                "kind": "experience",
                "modality": "required",
                "minimum_years": 5.0,
                "evidence": {"quote": "5年以上经验", "start": 0, "end": 10},
            },
        ],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_exp_01",
                "kind": "experience",
                "modality": "required",
                "minimum_years": 3.0,
                "evidence": {"quote": "3年以上经验", "start": 0, "end": 10},
            },
        ],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    si = [e for e in events if e["event_type"] == "seniority_inflation"]
    assert len(si) == 1
    assert si[0]["metrics"]["before_minimum_years"] == 5.0
    assert si[0]["metrics"]["after_minimum_years"] == 3.0
    assert si[0]["metrics"]["years_drop"] == 2.0
    assert si[0]["confidence_available"] is True
    assert si[0]["confidence_source"] == "computed"
    assert "5.0" in si[0]["reason"] and "3.0" in si[0]["reason"]


def test_seniority_inflation_no_false_positive_below_threshold():
    """经验年限下降不足 1 年时不触发。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_exp_01",
                "kind": "experience",
                "minimum_years": 3.0,
            },
        ],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_exp_01",
                "kind": "experience",
                "minimum_years": 2.5,
            },
        ],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    si = [e for e in events if e["event_type"] == "seniority_inflation"]
    assert len(si) == 0


def test_seniority_inflation_aggregate_fallback():
    """当 requirement_id 不匹配时，用聚合均值比较。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_exp_old",
                "kind": "experience",
                "minimum_years": 5.0,
            },
            {
                "requirement_id": "req_exp_old2",
                "kind": "experience",
                "minimum_years": 3.0,
            },
        ],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_exp_new",
                "kind": "experience",
                "minimum_years": 2.0,
            },
        ],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    si = [e for e in events if e["event_type"] == "seniority_inflation"]
    assert len(si) == 1
    assert si[0]["metadata"]["seniority_inflation"]["aggregation"] == "mean"
    # before avg = 4.0, after avg = 2.0, drop = 2.0 >= 1.0
    assert si[0]["metrics"]["before_avg_minimum_years"] == 4.0
    assert si[0]["metrics"]["after_avg_minimum_years"] == 2.0


def test_seniority_inflation_no_experience_requirements():
    """没有 experience 类 requirement 时不触发。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {"requirement_id": "req_skill", "kind": "skill", "skills": ["python"]},
        ],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {"requirement_id": "req_skill", "kind": "skill", "skills": ["python"]},
        ],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    si = [e for e in events if e["event_type"] == "seniority_inflation"]
    assert len(si) == 0


# ———— A19: credential_relaxation ————


def test_credential_relaxation_degree_drop():
    """学历要求从 master 降到 bachelor 时触发。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_edu_01",
                "kind": "education",
                "modality": "required",
                "minimum_degree": "master",
                "evidence": {"quote": "硕士及以上学历", "start": 0, "end": 10},
            },
        ],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_edu_01",
                "kind": "education",
                "modality": "required",
                "minimum_degree": "bachelor",
                "evidence": {"quote": "本科及以上学历", "start": 0, "end": 10},
            },
        ],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    cr = [e for e in events if e["event_type"] == "credential_relaxation"]
    assert len(cr) == 1
    assert cr[0]["metadata"]["credential_relaxation"]["before_degree"] == "master"
    assert cr[0]["metadata"]["credential_relaxation"]["after_degree"] == "bachelor"
    assert cr[0]["confidence_available"] is True


def test_credential_relaxation_certificate_drop():
    """证书要求减少时触发。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_cert_01",
                "kind": "certificate",
                "certificates": ["PMP", "AWS-SAA", "CKAD"],
            },
        ],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_cert_01",
                "kind": "certificate",
                "certificates": ["PMP"],
            },
        ],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    cr = [e for e in events if e["event_type"] == "credential_relaxation"]
    assert len(cr) == 1
    dropped = cr[0]["metadata"]["credential_relaxation"]["dropped_certificates"]
    assert "AWS-SAA" in dropped or "CKAD" in dropped
    assert cr[0]["metrics"]["dropped_certificate_count"] >= 1


def test_credential_relaxation_both_education_and_certificate():
    """同时有学历降级和证书减少时，生成多个事件。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_edu_01",
                "kind": "education",
                "minimum_degree": "doctorate",
            },
            {
                "requirement_id": "req_cert_01",
                "kind": "certificate",
                "certificates": ["CISSP", "CCSP"],
            },
        ],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_edu_01",
                "kind": "education",
                "minimum_degree": "master",
            },
            {
                "requirement_id": "req_cert_01",
                "kind": "certificate",
                "certificates": [],
            },
        ],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    cr = [e for e in events if e["event_type"] == "credential_relaxation"]
    assert len(cr) >= 2


def test_credential_relaxation_no_false_positive_same_degree():
    """学历要求不变时不触发。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_edu_01",
                "kind": "education",
                "minimum_degree": "bachelor",
            },
        ],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
        requirement_profile=[
            {
                "requirement_id": "req_edu_01",
                "kind": "education",
                "minimum_degree": "bachelor",
            },
        ],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    cr = [e for e in events if e["event_type"] == "credential_relaxation"]
    assert len(cr) == 0


def test_credential_relaxation_empty_requirement_profile():
    """requirement_profile 为空时不触发。"""
    before = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
    )
    after = _snapshot_with_requirements(
        [_relation("PY", "Python", 0.5, "LANG")],
    )
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2,
    )
    cr = [e for e in events if e["event_type"] == "credential_relaxation"]
    assert len(cr) == 0


def test_a19_events_present_in_event_types():
    """验证 A19 事件类型已注册。"""
    from app.domain.evolution import EVENT_TYPES
    assert "seniority_inflation" in EVENT_TYPES
    assert "credential_relaxation" in EVENT_TYPES


# ── coverage comparability gates：降低误检 ──


def test_sparse_before_version_suppresses_emergence():
    """before 版本样本量不足（<min_comparable_samples）时，技能"新兴"不可靠，应抑制。"""
    before = _snapshot([])
    before["sample_stats"] = {"included_samples": 2}
    after = _snapshot([_relation("RAG", "RAG", 0.5, "AI")])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_emergence" not in {e["event_type"] for e in events}


def test_sparse_after_version_suppresses_decline():
    """after 版本样本量不足时，技能"衰退"不可靠，应抑制。"""
    before = _snapshot([_relation("TF", "TensorFlow", 0.6, "ML")])
    after = _snapshot([])
    after["sample_stats"] = {"included_samples": 2}
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_decline" not in {e["event_type"] for e in events}


def test_low_support_skill_suppresses_emergence():
    """新技能只有 1 份支持文档时属于采样波动，不应判为新兴。"""
    before = _snapshot([])
    low_support = _relation("RUST", "Rust", 0.5, "LANG")
    low_support["statistics"]["support_document_count"] = 1
    after = _snapshot([low_support])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_emergence" not in {e["event_type"] for e in events}


def test_support_two_skill_suppressed_as_fluctuation():
    """支持文档数为 2（<min_support_documents=3）的技能出现/消失属采样波动。"""
    before = _snapshot([])
    two_doc = _relation("HB", "Hibernate", 0.3, "FRAMEWORK")
    two_doc["statistics"]["support_document_count"] = 2
    after = _snapshot([two_doc])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_emergence" not in {e["event_type"] for e in events}


def test_support_three_skill_still_emits_emergence():
    """支持文档数为 3（>=min_support_documents）的技能应正常判为新兴。"""
    before = _snapshot([])
    three_doc = _relation("RAG", "RAG", 0.5, "AI")
    three_doc["statistics"]["support_document_count"] = 3
    after = _snapshot([three_doc])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_emergence" in {e["event_type"] for e in events}


def test_low_support_weight_drop_suppressed_as_fluctuation():
    """两版本都在、但支持文档过少（<3）的权重下降属采样波动，不应判衰退。"""
    before = _snapshot([
        _relation_with_stats("KAF", "Kafka", 0.25, "INFRA",
                             support_docs=2, source_div=1, enterprise_cov=1),
    ])
    after = _snapshot([
        _relation_with_stats("KAF", "Kafka", 0.12, "INFRA",
                             support_docs=2, source_div=1, enterprise_cov=1),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_decline" not in {e["event_type"] for e in events}


def test_well_supported_weight_drop_still_emits_decline():
    """支持文档充足（>=3）的权重下降应正常判为衰退。"""
    before = _snapshot([
        _relation_with_stats("TF", "TensorFlow", 0.6, "ML",
                             support_docs=10, source_div=3, enterprise_cov=3),
    ])
    after = _snapshot([
        _relation_with_stats("TF", "TensorFlow", 0.2, "ML",
                             support_docs=10, source_div=3, enterprise_cov=3),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_decline" in {e["event_type"] for e in events}


def test_coverage_shift_suppresses_weight_driven_emergence():
    """权重变化主要由覆盖指标变化驱动时，只发 coverage_artifact，不发 skill_emergence。"""
    before = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=2, enterprise_cov=2),
    ])
    after = _snapshot([
        _relation_with_stats("PY", "Python", 0.55, "LANG",
                             support_docs=8, source_div=4, enterprise_cov=5),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    types = {e["event_type"] for e in events}
    assert "coverage_artifact" in types
    assert "skill_emergence" not in types


def test_missing_evidence_skill_still_emits_emergence():
    """缺失统计信息的技能（非低支持）不应被覆盖门禁误杀。"""
    before = _snapshot([])
    no_evidence_skill = {
        "skill_id": "GO",
        "canonical_name": "Go",
        "auto_weight": 0.5,
        "final_weight": 0.5,
        "auto_confidence": 0.8,
        "final_confidence": 0.8,
        "category_code": "LANG",
        "importance_level": "important",
    }
    after = _snapshot([no_evidence_skill])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_emergence" in {e["event_type"] for e in events}


def test_single_source_skill_disappearance_suppressed():
    """单源技能的消失属采样波动（唯一来源未覆盖），不应判衰退。"""
    before = _snapshot([
        _relation_with_stats("REDIS", "Redis", 0.4, "INFRA",
                             support_docs=4, source_div=1, enterprise_cov=2),
    ])
    after = _snapshot([])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_decline" not in {e["event_type"] for e in events}


def test_single_source_skill_emergence_suppressed():
    """单源新技能的出现属采样波动，不应判为真实演化。"""
    before = _snapshot([])
    after = _snapshot([
        _relation_with_stats("GO", "Go", 0.5, "LANG",
                             support_docs=4, source_div=1, enterprise_cov=2),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_emergence" not in {e["event_type"] for e in events}


def test_multi_source_skill_disappearance_emits_decline():
    """多源技能的消失应正常判为衰退。"""
    before = _snapshot([
        _relation_with_stats("REDIS", "Redis", 0.4, "INFRA",
                             support_docs=6, source_div=3, enterprise_cov=3),
    ])
    after = _snapshot([])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_decline" in {e["event_type"] for e in events}


def test_single_source_weight_drop_suppressed():
    """单源技能在两版本都存在但权重下降属采样波动，不应判衰退。"""
    before = _snapshot([
        _relation_with_stats("C", "C", 0.31, "LANG",
                             support_docs=5, source_div=1, enterprise_cov=4),
    ])
    after = _snapshot([
        _relation_with_stats("C", "C", 0.06, "LANG",
                             support_docs=3, source_div=1, enterprise_cov=2),
    ])
    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    assert "skill_decline" not in {e["event_type"] for e in events}


def test_coverage_noise_emergence_does_not_expand_role_breadth():
    before = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=2, enterprise_cov=2),
    ])
    after = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=2, enterprise_cov=2),
        _relation_with_stats("RAG", "RAG", 0.5, "AI",
                             support_docs=1, source_div=1, enterprise_cov=1),
    ])

    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    event_types = {event["event_type"] for event in events}

    assert "skill_emergence" not in event_types
    assert "role_expansion" not in event_types


def test_coverage_noise_decline_does_not_contract_role_breadth():
    before = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=2, enterprise_cov=2),
        _relation_with_stats("RAG", "RAG", 0.5, "AI",
                             support_docs=1, source_div=1, enterprise_cov=1),
    ])
    after = _snapshot([
        _relation_with_stats("PY", "Python", 0.4, "LANG",
                             support_docs=5, source_div=2, enterprise_cov=2),
    ])

    events = detect_evolution_events(
        before, after, position_id=POSITION_ID, from_version_id=1, to_version_id=2
    )
    event_types = {event["event_type"] for event in events}

    assert "skill_decline" not in event_types
    assert "role_contraction" not in event_types
