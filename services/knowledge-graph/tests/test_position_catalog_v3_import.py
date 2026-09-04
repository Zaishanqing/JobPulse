import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.models import GraphBuildRun, GraphVersion, StandardPosition
from jobgraph_contracts.position_catalog_v3 import (
    ResolvedPositionCatalogItemV3,
    build_resolved_position_catalog_v3,
)
from scripts import import_resolved_position_catalog as importer


def _item(**overrides) -> dict[str, object]:
    item = {
        "main_system_position_id": "main-position-uuid",
        "position_code": "BACKEND_ENGINEER",
        "position_name": "后端开发工程师",
        "family_code": "SOFTWARE_ENGINEERING",
        "family_name": "软件研发",
        "definition": "负责后端系统研发。",
        "aliases": ["后端工程师"],
        "include_when": ["核心职责为后端研发"],
        "exclude_when": ["仅包含相似标题"],
        "confusable_with": [],
        "lifecycle_status": "active",
        "deprecated_at": None,
        "replaced_by": None,
        "sample_support_status": "sufficient",
    }
    item.update(overrides)
    return item


def _write_catalog(path, positions: list[dict[str, object]]) -> None:
    catalog = build_resolved_position_catalog_v3(positions)
    path.write_text(catalog.model_dump_json(), encoding="utf-8")


def test_catalog_import_migrates_legacy_identity_and_graph_references(db, tmp_path, monkeypatch):
    db.add(
        StandardPosition(
            position_id="main-position-uuid",
            name="后端开发工程师",
            category_code="SOFTWARE_ENGINEERING",
        )
    )
    db.flush()
    db.add(
        GraphBuildRun(
            position_id="main-position-uuid",
            status="pending",
            config_snapshot={},
            summary={},
        )
    )
    db.flush()
    build_run = db.query(GraphBuildRun).one()
    db.add(
        GraphVersion(
            position_id="main-position-uuid",
            build_run_id=build_run.id,
            version_number=1,
            version_name="v1",
            snapshot={},
            source_version="source-v1",
            algorithm_version="weighted-v1",
            normalization_map_version="normalization-v1",
        )
    )
    db.commit()
    db.execute(text("PRAGMA foreign_keys=OFF"))
    db.execute(
        text(
            "INSERT INTO relation_claims "
            "(claim_id, graph_version_id, build_run_id, support_id, subject_id, "
            "predicate, object_id, claim_kind, source_kind, source_fact_id, "
            "source_fact_version, requirement_id, evidence_refs, "
            "catalog_snapshot_lineage_version, mapping_policy_version, observed_at, "
            "lineage_version, created_at) "
            "VALUES ('claim-1', 1, 1, 1, 'main-position-uuid', 'requires', "
            "'SKILL_TEST', 'observed', 'published_fact', 'fact-1', 'v1', "
            "'requirement-1', '[]', 'catalog-v1', 'mapping-v1', "
            "'2026-08-09T00:00:00Z', 'lineage-v1', '2026-08-09T00:00:00Z')"
        )
    )
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    for table_name in ("graph_versions", "relation_claims"):
        db.execute(
            text(
                f"CREATE TRIGGER trg_{table_name}_reject_update "
                f"BEFORE UPDATE ON {table_name} "
                f"BEGIN SELECT RAISE(ABORT, '{table_name} is immutable'); END"
            )
        )
    db.commit()
    catalog = tmp_path / "positions.json"
    _write_catalog(catalog, [_item()])
    monkeypatch.setattr(
        importer,
        "create_database",
        lambda _settings: SimpleNamespace(
            session_factory=lambda: db,
            engine=SimpleNamespace(dispose=lambda: None),
        ),
    )

    result = importer.import_catalog(catalog)

    assert result["migrated_identity_count"] == 1
    assert db.query(StandardPosition).one().position_id == "BACKEND_ENGINEER"
    assert db.query(GraphBuildRun).one().position_id == "BACKEND_ENGINEER"
    assert db.query(GraphVersion).one().position_id == "BACKEND_ENGINEER"
    assert (
        db.execute(text("SELECT subject_id FROM relation_claims")).scalar_one()
        == "BACKEND_ENGINEER"
    )


def test_catalog_import_updates_authoritative_lifecycle_fields(db, tmp_path, monkeypatch):
    db.add(
        StandardPosition(
            position_id="BACKEND_ENGINEER",
            position_code="BACKEND_ENGINEER",
            name="旧名称",
            category_code="OLD_FAMILY",
            taxonomy_version="position-taxonomy.v3.0.0",
            sample_support_status="sufficient",
            status="active",
        )
    )
    db.commit()
    catalog = tmp_path / "positions.json"
    _write_catalog(
        catalog,
        [
            _item(
                position_name="后端研发工程师",
                lifecycle_status="deprecated",
                deprecated_at="2026-08-09",
                sample_support_status="sparse",
            )
        ],
    )
    monkeypatch.setattr(
        importer,
        "create_database",
        lambda _settings: SimpleNamespace(
            session_factory=lambda: db,
            engine=SimpleNamespace(dispose=lambda: None),
        ),
    )

    result = importer.import_catalog(catalog)

    position = db.query(StandardPosition).one()
    assert result["updated_count"] == 1
    assert position.name == "后端研发工程师"
    assert position.category_code == "SOFTWARE_ENGINEERING"
    assert position.status == "deprecated"
    assert position.sample_support_status == "sparse"


def test_catalog_import_rolls_back_all_identity_changes_on_incomplete_mapping(
    db, tmp_path, monkeypatch
):
    db.add_all(
        [
            StandardPosition(
                position_id="main-position-uuid",
                name="后端开发工程师",
                category_code="SOFTWARE_ENGINEERING",
            ),
            StandardPosition(
                position_id="unmapped-main-id",
                name="未映射岗位",
                category_code="SOFTWARE_ENGINEERING",
            ),
        ]
    )
    db.commit()
    catalog = tmp_path / "positions.json"
    _write_catalog(catalog, [_item()])
    monkeypatch.setattr(
        importer,
        "create_database",
        lambda _settings: SimpleNamespace(
            session_factory=lambda: db,
            engine=SimpleNamespace(dispose=lambda: None),
        ),
    )

    with pytest.raises(ValueError, match="absent from the authoritative"):
        importer.import_catalog(catalog)

    rows = db.execute(
        text("SELECT position_id, position_code FROM standard_positions ORDER BY position_id")
    ).all()
    assert rows == [("main-position-uuid", None), ("unmapped-main-id", None)]


@pytest.mark.parametrize("duplicate_field", ["main_system_position_id", "position_code"])
def test_catalog_import_rejects_duplicate_identity(tmp_path, duplicate_field):
    overrides = {
        "position_name": "前端开发工程师",
        "position_code": "FRONTEND_ENGINEER",
        "main_system_position_id": "main-position-uuid-2",
    }
    overrides[duplicate_field] = _item()[duplicate_field]
    items = [
        ResolvedPositionCatalogItemV3.model_validate(_item()),
        ResolvedPositionCatalogItemV3.model_validate(_item(**overrides)),
    ]
    catalog = tmp_path / "positions.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": "resolved-position-catalog.v3",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "position_count": 2,
                "positions": [item.model_dump(mode="json") for item in items],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=f"{duplicate_field} must be unique"):
        importer.import_catalog(catalog)
