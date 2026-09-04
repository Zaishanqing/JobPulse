from __future__ import annotations

import json

from app.models import GraphVersion
from scripts.verify_contract_baseline import (
    DEFAULT_REPOSITORY_ROOT,
    INVENTORY_PATH,
    normalize_json_schema,
    verify_baseline,
)
from tests.factories import valid_build


def _inventory():
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _published_snapshot_contract():
    return next(
        item
        for item in _inventory()["declarative_contracts"]
        if item["id"] == "kg.graph-version.snapshot.v1"
    )


def _evidence(value):
    if isinstance(value, dict):
        if isinstance(value.get("evidence"), dict):
            yield value["evidence"]
        for child in value.values():
            yield from _evidence(child)
    elif isinstance(value, list):
        for child in value:
            yield from _evidence(child)


def test_schema_normalization_removes_explicit_true_defaults_recursively():
    assert normalize_json_schema(
        {
            "additionalProperties": True,
            "nested": [{"additionalProperties": False}],
        }
    ) == {"nested": [{"additionalProperties": False}]}


def test_checked_in_phase0_baseline_matches_local_and_parent_contracts():
    verify_baseline(include_external=False)
    verify_baseline(
        include_external=True,
        repository_root=DEFAULT_REPOSITORY_ROOT,
    )


def test_phase0_inventory_contains_no_synthetic_demo_artifacts():
    assert all(
        "synthetic" not in item["id"] and "demo" not in item["owner"]
        for item in _inventory()["artifacts"]
    )


def test_published_graph_snapshot_matches_phase0_contract(client, db, auth_headers):
    headers = auth_headers()
    build = valid_build(client, db, headers, doc_id="PHASE0_SNAPSHOT")
    published = client.post(
        f"/api/v1/graph/build-runs/{build['build_run_id']}/publish",
        json={"version_name": "phase0-v1"},
        headers=headers,
    )
    assert published.status_code == 200, published.text
    version = db.get(GraphVersion, published.json()["data"]["version_id"])
    expected_fields = _published_snapshot_contract()["root_fields"]
    assert list(version.snapshot) == expected_fields
    assert version.source_version.startswith("weighted-v1:build:")
    assert len(version.source_version) <= 128


def test_phase0_inventory_tracks_v3_validation_and_catalog_snapshots():
    inventory = _inventory()
    published = next(
        item
        for item in inventory["runtime_contracts"]
        if item["id"] == "kg.published-jd-fact.v3.runtime"
    )
    assert {
        "validation_lineage",
        "skill_catalog_snapshot",
        "position_catalog_snapshot",
    }.issubset(published["root_fields"])
