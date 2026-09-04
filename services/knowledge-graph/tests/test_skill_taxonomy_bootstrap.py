from pathlib import Path

from scripts.sync_skill_taxonomy_catalog import load_snapshots


ROOT = Path(__file__).resolve().parents[1]


def test_reviewed_catalog_builds_complete_v2_snapshots():
    taxonomy_version, snapshots = load_snapshots(
        ROOT / "config" / "skill_taxonomy_catalog.v1.json"
    )

    assert len(snapshots) == 1122
    assert taxonomy_version == "skill-taxonomy-catalog-current"
    assert {snapshot.taxonomy_version for snapshot in snapshots} == {taxonomy_version}
    assert all(snapshot.classifications for snapshot in snapshots)
    assert all(
        relation.name_zh
        for snapshot in snapshots
        for relation in snapshot.classifications
    )


def test_image_package_discovery_includes_shared_contracts():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'include = ["app*", "jobgraph_contracts*"]' in pyproject
