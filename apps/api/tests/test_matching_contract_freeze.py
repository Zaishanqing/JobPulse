from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from jobgraph_contracts.matching import (
    CV_PROFILE_SCHEMA_VERSION,
    POSITION_PROFILE_SCHEMA_VERSION,
)


ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "matching-service"


def test_profile_schema_versions_are_identical_across_process_and_service_boundary():
    probe = (
        "from app.domain.profiles import CVMatchProfile, PositionMatchProfile;"
        "print(CVMatchProfile.model_fields['schema_version'].default);"
        "print(PositionMatchProfile.model_fields['schema_version'].default)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=SERVICE_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    cv_version, position_version = completed.stdout.strip().splitlines()
    assert cv_version == CV_PROFILE_SCHEMA_VERSION
    assert position_version == POSITION_PROFILE_SCHEMA_VERSION


def test_matching_service_cannot_import_main_framework_orm_models():
    violations = []
    for path in (SERVICE_ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "app.models"
            ):
                violations.append((path.relative_to(SERVICE_ROOT), node.lineno))
            if isinstance(node, ast.Import):
                for name in node.names:
                    if name.name.startswith("app.models"):
                        violations.append((path.relative_to(SERVICE_ROOT), node.lineno))

    assert violations == []


def test_semantic_evidence_is_reserved_without_wiring_into_scoring():
    semantic = (SERVICE_ROOT / "app" / "domain" / "vector_contracts.py").read_text(
        "utf-8"
    )
    scoring = (SERVICE_ROOT / "app" / "domain" / "scoring.py").read_text("utf-8")
    matching = (SERVICE_ROOT / "app" / "domain" / "matching.py").read_text("utf-8")

    assert "class SemanticEvidence" in semantic
    assert "SemanticEvidence" not in scoring
    assert "SemanticEvidence" not in matching
