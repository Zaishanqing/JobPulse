from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from jobgraph_contracts.position_catalog_v3 import (
    build_resolved_position_catalog_v3,
)
from scripts import export_resolved_position_catalog, position_v3_cutover
from scripts.publish_offline_batch import _position_bindings


def _resolved_classification() -> dict[str, object]:
    return {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": "position-taxonomy.v3.0.0",
        "position_code": "BACKEND_ENGINEER",
        "position_name": "后端开发工程师",
        "family_code": "SOFTWARE_ENGINEERING",
        "family_name": "软件研发",
        "candidate_positions": [{"position_code": "BACKEND_ENGINEER", "score": 0.92}],
        "career_level": "senior",
        "leadership_scope": "none",
        "technology_focus_codes": ["CLOUD_NATIVE"],
        "industry_context_codes": [],
        "observed_skill_domain_codes": ["software_engineering"],
        "confidence": 0.92,
        "classification_status": "resolved",
        "review_reason_codes": [],
        "evidence_refs": ["evidence-1"],
        "classification_policy_version": "position-classifier.v3.0",
    }


def _catalog():
    return build_resolved_position_catalog_v3(
        [
            {
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
        ]
    )


def test_offline_position_binding_uses_code_and_returns_main_system_uuid():
    task = SimpleNamespace(
        bundle_payload={"normalized_result": {"job_classification": _resolved_classification()}}
    )
    database = SimpleNamespace(
        session_factory=lambda: nullcontext(SimpleNamespace(get=lambda model, task_id: task))
    )
    position = SimpleNamespace(
        position_id="main-position-uuid",
        position_code="BACKEND_ENGINEER",
        position_name="后端开发工程师",
        taxonomy_version="position-taxonomy.v3.0.0",
        lifecycle_status="active",
    )
    container = SimpleNamespace(positions=SimpleNamespace(list=lambda: [position]))

    assert _position_bindings(database, container, ["task-1"]) == {
        "BACKEND_ENGINEER": ("main-position-uuid", "后端开发工程师")
    }


def test_cutover_imports_catalog_before_exporting_and_importing_release(tmp_path, monkeypatch):
    jd_report = tmp_path / "jd-report.json"
    cv_report = tmp_path / "cv-report.json"
    jd_report.write_text(
        json.dumps(
            {
                "schema": "position-reclassification-report.v3",
                "catalog_version": "position-taxonomy.v3.0.0",
                "document_count": 2,
            }
        ),
        encoding="utf-8",
    )
    cv_report.write_text(
        json.dumps(
            {
                "schema": "cv-position-reclassification-report.v3",
                "catalog_version": "position-taxonomy.v3.0.0",
                "role_count": 1,
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        position_v3_cutover,
        "_run",
        lambda command, *, execute: commands.append(command),
    )
    monkeypatch.setattr(
        position_v3_cutover,
        "_post_reindex",
        lambda url, *, execute: None,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "position_v3_cutover.py",
            "--jd-report",
            str(jd_report),
            "--cv-report",
            str(cv_report),
            "--jd-run-dir",
            str(tmp_path / "jd-run"),
            "--cv-run-dir",
            str(tmp_path / "cv-run"),
            "--cv-workbook",
            str(tmp_path / "cv.xlsx"),
            "--position-catalog-output",
            str(tmp_path / "position-catalog.json"),
            "--kg-release-output",
            str(tmp_path / "kg-release.json"),
            "--release-id",
            "release-v3",
            "--publisher-id",
            "publisher-1",
            "--window-start",
            "2026-08-01T00:00:00Z",
            "--window-end",
            "2026-08-08T00:00:00Z",
            "--git-commit",
            "deadbeef",
        ],
    )

    assert position_v3_cutover.main() == 0
    scripts = [command[1] for command in commands]
    assert scripts == [
        "scripts/apply_position_v3_to_existing_jds.py",
        "scripts/import_precomputed_cv_results.py",
        "scripts/run_pending_validation_tasks.py",
        "scripts/publish_position_v3_migrated_jds.py",
        "scripts/export_resolved_position_catalog.py",
        "services/knowledge-graph/scripts/import_resolved_position_catalog.py",
        "scripts/export_kg_release.py",
        "services/knowledge-graph/scripts/import_release.py",
        "services/knowledge-graph/scripts/build_kg_graphs.py",
    ]

    (tmp_path / "position-catalog.json").write_text(
        _catalog().model_dump_json(),
        encoding="utf-8",
    )
    release_dir = tmp_path / "kg-release.json"
    release_dir.mkdir()
    (release_dir / "manifest.json").write_text(
        json.dumps(
            {
                "release_schema_version": "kg-release-manifest.v1",
                "release_id": "release-v3",
                "producer": {"git_commit": "deadbeef"},
                "mode": "full",
                "parent_release_id": None,
                "observation_window": {
                    "start": "2026-08-01T00:00:00Z",
                    "end": "2026-08-08T00:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    commands.clear()

    assert position_v3_cutover.main() == 0
    resumed_scripts = [command[1] for command in commands]
    catalog_command = next(
        command
        for command in commands
        if command[1] == "scripts/export_resolved_position_catalog.py"
    )
    assert "--verify-existing" in catalog_command
    assert "scripts/export_kg_release.py" not in resumed_scripts
    assert "services/knowledge-graph/scripts/import_resolved_position_catalog.py" in (
        resumed_scripts
    )
    assert "services/knowledge-graph/scripts/import_release.py" in resumed_scripts


def test_cutover_rejects_incomplete_existing_catalog(tmp_path):
    path = tmp_path / "position-catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "resolved-position-catalog.v3",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "positions": [{"position_code": "BACKEND_ENGINEER"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not reusable"):
        position_v3_cutover._validate_existing_catalog(path)


def test_existing_catalog_must_match_current_main_system_snapshot(
    tmp_path, monkeypatch
):
    output = tmp_path / "position-catalog.json"
    current = _catalog()
    stale = build_resolved_position_catalog_v3(
        [
            {
                **current.positions[0].model_dump(mode="json"),
                "position_name": "旧岗位名称",
            }
        ]
    )
    output.write_text(stale.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        export_resolved_position_catalog,
        "_authoritative_catalog",
        lambda: current,
    )

    with pytest.raises(ValueError, match="does not match"):
        export_resolved_position_catalog.export_catalog(
            output,
            verify_existing=True,
        )


def test_cutover_manifest_binds_catalog_count_and_codes_without_hash(tmp_path):
    path = tmp_path / "position-v3-cutover.json"
    catalog = _catalog()

    position_v3_cutover._bind_cutover_manifest(
        path,
        catalog=catalog,
        migration_run_id="migration-v3",
        release_id="release-v3",
        window_start="2026-08-01T00:00:00Z",
        window_end="2026-08-08T00:00:00Z",
        git_commit="deadbeef",
        mode="full",
        parent_release_id=None,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["position_catalog_count"] == 1
    assert payload["position_catalog_codes"] == ["BACKEND_ENGINEER"]
    assert all("hash" not in key.lower() for key in payload)


def test_catalog_preserves_structured_confusable_distinction():
    first = _catalog().positions[0].model_dump(mode="json")
    second = {
        **first,
        "main_system_position_id": "main-position-uuid-2",
        "position_code": "LLM_ALGORITHM_ENGINEER",
        "position_name": "大模型算法工程师",
        "aliases": ["LLM 算法工程师"],
    }
    first["confusable_with"] = [
        {
            "position_code": "LLM_ALGORITHM_ENGINEER",
            "distinguish_by": "是否以模型训练和算法优化为核心职责",
        }
    ]

    catalog = build_resolved_position_catalog_v3([first, second])

    edge = catalog.positions[0].confusable_with[0]
    assert edge.position_code == "LLM_ALGORITHM_ENGINEER"
    assert edge.distinguish_by == "是否以模型训练和算法优化为核心职责"
