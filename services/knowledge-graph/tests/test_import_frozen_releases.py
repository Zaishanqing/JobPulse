import json
from types import SimpleNamespace

from app.models import GraphBuildRun, GraphVersion, ReleaseImportBatch, StandardPosition
from scripts import import_frozen_releases as importer


def test_same_frozen_release_is_imported_only_once(db, tmp_path, monkeypatch):
    db.add(
        StandardPosition(
            position_id="BACKEND_ENGINEER",
            name="Backend Engineer",
            category_code="SOFTWARE_ENGINEERING",
        )
    )
    db.commit()

    manifest_path = tmp_path / "frozen-release.json"
    manifest_path.write_text(
        json.dumps(
            {
                "summary": {
                    "manifest_version": "A-DATA-01-v1",
                    "detector_version": "detector-v1",
                    "config_version": "config-v1",
                    "catalog_version": "catalog-v1",
                },
                "releases": [
                    {
                        "release_id": "REL-BACKEND-001",
                        "position_id": "POS_JAVA_BACKEND",
                        "position_name": "Backend Engineer",
                        "graph_version_id": "GV-POS_JAVA_BACKEND-001",
                        "graph_version_number": 1,
                        "time_window": {"start": None, "end": None},
                        "sample_count": 1,
                        "skill_count": 1,
                        "responsibility_count": 1,
                        "detector_version": "detector-v1",
                        "config_version": "config-v1",
                        "catalog_version_id": "catalog-v1",
                    }
                ],
                "version_pairs": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(importer, "_check_migration", lambda _session: None)
    monkeypatch.setattr(
        importer,
        "create_database",
        lambda _settings: SimpleNamespace(
            session_factory=lambda: db,
            engine=SimpleNamespace(dispose=lambda: None),
        ),
    )

    importer.import_frozen_releases(manifest_path)
    first_current_version_id = db.query(StandardPosition).one().current_version_id
    first_counts = (
        db.query(GraphBuildRun).count(),
        db.query(GraphVersion).count(),
        db.query(ReleaseImportBatch).count(),
    )
    assert first_counts == (1, 1, 1)
    assert first_current_version_id is not None

    second_result = importer.import_frozen_releases(manifest_path)

    assert second_result["build_runs_created"] == 0
    assert second_result["graph_versions_created"] == 0
    assert second_result["release_batches_created"] == 0
    assert db.query(GraphBuildRun).count() == first_counts[0]
    assert db.query(GraphVersion).count() == first_counts[1]
    assert db.query(StandardPosition).one().current_version_id == first_current_version_id
    assert db.query(ReleaseImportBatch).count() == first_counts[2]


def test_published_frozen_graph_returns_unified_portal_contract(db):
    position = StandardPosition(
        position_id="BACKEND_ENGINEER",
        name="Backend Engineer",
        category_code="SOFTWARE_ENGINEERING",
    )
    db.add(position)
    db.flush()
    run = GraphBuildRun(
        position_id="BACKEND_ENGINEER",
        status="published",
        config_snapshot={},
        summary={"sample_count": 3, "skill_count": 2, "source_count": 1},
    )
    db.add(run)
    db.flush()
    version = GraphVersion(
        position_id="BACKEND_ENGINEER",
        build_run_id=run.id,
        version_number=1,
        version_name="v1-frozen",
        snapshot={
            "position_id": "BACKEND_ENGINEER",
            "position_name": "Backend Engineer",
            "time_window": {"start": "2026-08-07", "end": "2026-08-07"},
            "sample_count": 3,
            "skill_count": 2,
        },
        source_version="evolution-defaults-v1",
        algorithm_version="position-evolution-events-v1",
        normalization_map_version="evolution-defaults-v1",
        published_fact_versions=[],
    )
    db.add(version)
    db.flush()
    position.current_version_id = version.id
    db.commit()

    from app.infrastructure.sqlalchemy.query_graphs import GraphQueryMixin

    class _Queries(GraphQueryMixin):
        def __init__(self, session):
            self.session = session

    graph = _Queries(db).graph("BACKEND_ENGINEER")
    assert graph["position"] == {
        "position_id": "BACKEND_ENGINEER",
        "name": "Backend Engineer",
        "category_code": "SOFTWARE_ENGINEERING",
    }
    assert graph["sample_stats"]["included_samples"] == 3
    assert graph["sample_stats"]["relations"] == 2
    assert graph["sample_stats"]["minimum_valid_samples"] == 1
    assert graph["requirement_profile"] == []
    assert graph["company_context"] == []
    assert graph["employment_context"] == []
    assert graph["skill_relations"] == []
