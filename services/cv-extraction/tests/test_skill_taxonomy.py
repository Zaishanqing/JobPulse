from __future__ import annotations

from pathlib import Path

from src.normalizer import load_normalization_map
from src.skill_taxonomy import (
    build_classification_records,
    load_skill_taxonomy_snapshot,
    validate_snapshot_against_normalization_map,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = (
    WORKSPACE_ROOT / "resources" / "taxonomy" / "2.0" / "skill_taxonomy_snapshot.json"
)
NORMALIZATION_PATH = (
    WORKSPACE_ROOT / "resources" / "normalization" / "2.0" / "normalization_map.yaml"
)


def test_cv_uses_the_reviewed_shared_skill_taxonomy_snapshot():
    snapshot = load_skill_taxonomy_snapshot(SNAPSHOT_PATH)
    normalization_map = load_normalization_map(str(NORMALIZATION_PATH))

    validate_snapshot_against_normalization_map(snapshot, normalization_map)
    records, summary = build_classification_records(
        [
            {
                "document_id": "cv_1",
                "source_scope": "skills",
                "source_item_id": "skill_1",
                "source_name": "PyTorch",
                "skill_id": "FRAMEWORK_PYTORCH",
                "canonical_name": "PyTorch",
                "identity_resolution_status": "resolved",
            }
        ],
        snapshot,
    )

    assert summary["classification_resolved_count"] == 1
    assert {
        (relation["facet"], relation["code"])
        for relation in records[0]["classifications"]
    } == {
        ("concept_class", "technology"),
        ("technology_kind", "framework"),
        ("domain", "ai_intelligent_systems"),
    }
