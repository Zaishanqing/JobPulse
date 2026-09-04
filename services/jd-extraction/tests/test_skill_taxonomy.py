from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.exceptions import InputFormatError
from src.normalizer import load_normalization_map
from src.skill_taxonomy import (
    build_classification_records,
    load_skill_taxonomy_snapshot,
    validate_snapshot_against_normalization_map,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PROJECT_ROOT / "config" / "skill_taxonomy_snapshot.json"
NORMALIZATION_PATH = PROJECT_ROOT / "config" / "normalization_map.yaml"


def _codes(snapshot: dict, skill_id: str) -> set[tuple[str, str]]:
    return {
        (relation["facet"], relation["code"])
        for relation in snapshot["skills"][skill_id]["classifications"]
    }


def test_reviewed_snapshot_matches_normalization_and_design_examples():
    snapshot = load_skill_taxonomy_snapshot(SNAPSHOT_PATH)
    normalization_map = load_normalization_map(str(NORMALIZATION_PATH))

    validate_snapshot_against_normalization_map(snapshot, normalization_map)
    assert len(snapshot["skills"]) == 1122
    assert all(
        entry["review"]["status"] == "approved"
        and entry["review"]["review_basis"] == "canonical_identity"
        and entry["review"]["domain_decision"] in {
            "classified",
            "not_applicable",
        }
        for entry in snapshot["skills"].values()
    )

    assert _codes(snapshot, "LANG_PYTHON") >= {
        ("concept_class", "technology"),
        ("technology_kind", "language"),
        ("domain", "software_engineering"),
        ("domain", "data_engineering"),
        ("domain", "ai_intelligent_systems"),
    }
    assert _codes(snapshot, "AI_LLM") == {
        ("concept_class", "technology"),
        ("technology_kind", "algorithm_model"),
        ("domain", "ai_intelligent_systems"),
    }
    assert _codes(snapshot, "AI_RAG") == {
        ("concept_class", "practice"),
        ("domain", "ai_intelligent_systems"),
        ("domain", "data_engineering"),
    }
    assert _codes(snapshot, "KNOWLEDGE_LLM_PRINCIPLES") == {
        ("concept_class", "knowledge"),
        ("domain", "ai_intelligent_systems"),
    }
    assert _codes(snapshot, "TOOL_KUBERNETES") == {
        ("concept_class", "technology"),
        ("technology_kind", "platform_service"),
        ("domain", "cloud_distributed"),
    }
    assert _codes(snapshot, "KNOWLEDGE_DISTRIBUTED_SYSTEMS") == {
        ("concept_class", "knowledge"),
        ("domain", "cloud_distributed"),
    }
    assert _codes(snapshot, "METHOD_FEW_SHOT") == {
        ("concept_class", "practice"),
        ("domain", "ai_intelligent_systems"),
    }
    assert _codes(snapshot, "KNOWLEDGE_ENGLISH") == {
        ("concept_class", "transversal_skill"),
    }


def test_projection_separates_identity_and_classification_resolution():
    snapshot = load_skill_taxonomy_snapshot(SNAPSHOT_PATH)
    records, summary = build_classification_records(
        [
            {
                "document_id": "jd_1",
                "source_scope": "requirement:req_1",
                "source_name": "Python",
                "skill_id": "LANG_PYTHON",
                "canonical_name": "Python",
                "identity_resolution_status": "resolved",
            },
            {
                "document_id": "jd_1",
                "source_scope": "requirement:req_2",
                "source_name": "未知技能",
                "skill_id": None,
                "canonical_name": None,
                "identity_resolution_status": "unresolved",
            },
            {
                "document_id": "jd_1",
                "source_scope": "requirement:req_3",
                "source_name": "已解析但未纳入新分类的技能",
                "skill_id": "LEGACY_UNKNOWN",
                "canonical_name": "Legacy Unknown",
                "identity_resolution_status": "resolved",
            },
        ],
        snapshot,
    )

    assert records[0]["classification_resolution_status"] == "resolved"
    assert records[1]["classification_unresolved_reason"] == "identity_unresolved"
    assert records[2]["classification_unresolved_reason"] == "classification_missing"
    assert records[2]["classifications"] == []
    assert summary == {
        "classification_occurrence_count": 3,
        "classification_resolved_count": 1,
        "classification_missing_count": 1,
        "classification_identity_unresolved_count": 1,
    }


def test_snapshot_allows_reviewed_identity_before_classification(tmp_path: Path):
    snapshot = load_skill_taxonomy_snapshot(SNAPSHOT_PATH)
    normalization_map = load_normalization_map(str(NORMALIZATION_PATH))
    normalization_map["skills"]["ReviewedNewIdentity"] = {
        "skill_id": "TOOL_REVIEWED_NEW_IDENTITY",
        "canonical_name": "Reviewed New Identity",
        "category_code": "tool",
        "subcategory_code": "OTHER",
    }

    validate_snapshot_against_normalization_map(snapshot, normalization_map)

    records, summary = build_classification_records(
        [{
            "document_id": "jd_1",
            "source_scope": "requirement:req_1",
            "source_name": "ReviewedNewIdentity",
            "skill_id": "TOOL_REVIEWED_NEW_IDENTITY",
            "canonical_name": "Reviewed New Identity",
            "identity_resolution_status": "resolved",
        }],
        snapshot,
    )
    assert records[0]["classification_resolution_status"] == "unresolved"
    assert records[0]["classification_unresolved_reason"] == "classification_missing"
    assert summary["classification_missing_count"] == 1


def test_snapshot_rejects_technology_without_kind(tmp_path: Path):
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    snapshot["skills"]["LANG_PYTHON"]["classifications"] = [
        {
            "facet": "concept_class",
            "code": "technology",
            "is_primary": True,
        }
    ]
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(InputFormatError, match="technology_kind conflicts"):
        load_skill_taxonomy_snapshot(invalid)
