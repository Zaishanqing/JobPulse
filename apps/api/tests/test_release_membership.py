from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from app.contexts.insight_cards.release_registry import (
    ManifestReleaseRegistry,
    ReleaseArtifactInvalid,
)


def _fact(
    source_jd_id: str = "jd-1",
    source_fact_id: str = "fact-1",
    source_fact_version: str = "2026-07-10T00:00:00+00:00",
) -> dict:
    classification = {
        "schema_version": "job-position-classification.v3",
        "taxonomy_version": "position-taxonomy.v3.0.0",
        "position_code": "BACKEND_ENGINEER",
        "position_name": "后端开发工程师",
        "family_code": "SOFTWARE_ENGINEERING",
        "family_name": "软件研发",
        "candidate_positions": [
            {"position_code": "BACKEND_ENGINEER", "score": 0.95}
        ],
        "career_level": "senior",
        "leadership_scope": None,
        "technology_focus_codes": [],
        "industry_context_codes": [],
        "observed_skill_domain_codes": ["software_engineering"],
        "confidence": 0.95,
        "classification_status": "resolved",
        "review_reason_codes": [],
        "evidence_refs": ["evidence-1"],
        "classification_policy_version": "position-classifier.v3.0",
    }
    snapshot = {
        "content_hash": "a1b2c3d4" * 8,
        "effective_at": "2026-07-10T00:00:00+00:00",
        "status": "active",
    }
    return {
        "contract_version": "published-jd-fact.v3",
        "schema_version": "v2",
        "source_system": "main-system",
        "source_jd_id": source_jd_id,
        "source_fact_id": source_fact_id,
        "source_fact_version": source_fact_version,
        "review_status": "published",
        "published_at": "2026-07-10T00:00:00+00:00",
        "position_fact": classification,
        "skill_facts": [],
        "requirement_facts": [],
        "company_facts": [],
        "employment_facts": [],
        "evidence": [],
        "extraction_fact": {"schema_version": "v2", "document_id": source_jd_id},
        "normalized_fact": {
            "schema_version": "v2",
            "document_id": source_jd_id,
            "job_classification": classification,
        },
        "trace_metadata": {},
        "validation_lineage": {
            "state": "absent",
            "absent_reason": "validation_not_enforced",
        },
        "skill_catalog_snapshot": {
            **snapshot,
            "source": "main-system-skill-catalog",
            "catalog_version": "skill-taxonomy-catalog.v1",
        },
        "position_catalog_snapshot": {
            **snapshot,
            "source": "main-system-position-catalog",
            "catalog_version": "position-taxonomy.v3.0.0",
        },
    }


def _release(tmp_path: Path, rows: list[dict], *, record_count: int | None = None) -> Path:
    base = tmp_path / "releases"
    target = base / "REL-1"
    target.mkdir(parents=True)
    manifest = {
        "release_schema_version": "kg-release-manifest.v1",
        "release_id": "REL-1",
        "created_at": "2026-08-01T00:00:00+00:00",
        "producer": {"application": "knowledge-graph", "git_commit": "abc"},
        "mode": "full",
        "observation_window": {
            "start": "2026-07-01T00:00:00+00:00",
            "end": "2026-07-31T23:59:59+00:00",
        },
        "artifacts": [
            {
                "artifact_type": "published-jd-facts",
                "contract_version": "published-jd-fact.v3",
                "path": "facts.jsonl.gz",
                "record_count": len(rows) if record_count is None else record_count,
            }
        ],
    }
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with gzip.open(target / "facts.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return base


def test_registry_validates_exact_release_membership(tmp_path: Path) -> None:
    registry = ManifestReleaseRegistry(_release(tmp_path, [_fact()]))
    identity = registry.resolve("REL-1")
    assert registry.evidence_belongs_to_release(
        identity,
        source_jd_id="jd-1",
        source_fact_id="fact-1",
        source_version="2026-07-10T00:00:00+00:00",
    )
    assert not registry.evidence_belongs_to_release(
        identity,
        source_jd_id="jd-1",
        source_fact_id="fact-1",
        source_version="2026-07-11T00:00:00+00:00",
    )


def test_registry_rejects_record_count_mismatch(tmp_path: Path) -> None:
    registry = ManifestReleaseRegistry(_release(tmp_path, [_fact()], record_count=2))
    with pytest.raises(ReleaseArtifactInvalid, match="record_count mismatch"):
        registry.resolve("REL-1")


def test_registry_rejects_duplicate_membership_key(tmp_path: Path) -> None:
    registry = ManifestReleaseRegistry(_release(tmp_path, [_fact(), _fact()]))
    with pytest.raises(ReleaseArtifactInvalid, match="duplicate release membership"):
        registry.resolve("REL-1")


def test_registry_rejects_corrupt_gzip(tmp_path: Path) -> None:
    base = _release(tmp_path, [_fact()])
    (base / "REL-1" / "facts.jsonl.gz").write_text("not gzip", encoding="utf-8")
    with pytest.raises(ReleaseArtifactInvalid, match="unreadable"):
        ManifestReleaseRegistry(base).resolve("REL-1")


def test_registry_does_not_cache_failed_release(tmp_path: Path) -> None:
    base = _release(tmp_path, [_fact()], record_count=2)
    registry = ManifestReleaseRegistry(base)
    with pytest.raises(ReleaseArtifactInvalid):
        registry.resolve("REL-1")
    manifest_path = base / "REL-1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["record_count"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert registry.resolve("REL-1").release_id == "REL-1"
