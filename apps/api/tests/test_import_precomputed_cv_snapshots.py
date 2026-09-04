from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "import_precomputed_cv_snapshots.py"
SPEC = importlib.util.spec_from_file_location("import_precomputed_cv_snapshots", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _evidence(document_id: str = "cv_000001") -> dict[str, object]:
    return {
        "source_document_id": document_id,
        "source_id": "source-1",
        "quote": "Python",
        "start": 0,
        "end": 6,
        "alignment": "exact",
        "occurrence_index": 0,
    }


def test_build_response_rebinds_document_and_maps_v3_fields() -> None:
    evidence = _evidence()
    record = {
        "cv_id": "cv_000001",
        "annotation": {
            "document_id": "cv_000001",
            "personal_info": None,
            "education": [],
            "work_experience": [],
            "project_experience": [],
            "skills": [
                {
                    "item_id": "skill-1",
                    "name": "Python",
                    "item_type": "programming_language",
                    "proficiency": None,
                    "evidence": evidence,
                }
            ],
            "languages": [],
            "certificates": [],
            "awards": [],
            "self_evaluation": [],
        },
        "review_flags": [],
    }
    normalized = {
        "document_id": "cv_000001",
        "normalized_skills": [
            {
                "source_item_id": "skill-1",
                "source_scope": "skills",
                "source_name": "Python",
                "skill_id": "LANG_PYTHON",
                "canonical_name": "Python",
                "identity_resolution_status": "resolved",
                "classifications": [
                    {
                        "facet": "concept_class",
                        "code": "technology",
                        "is_primary": True,
                    },
                    {
                        "facet": "technology_kind",
                        "code": "language",
                        "is_primary": True,
                    },
                ],
            }
        ],
        "unresolved_items": [],
    }
    response = MODULE.build_response(
        record,
        normalized,
        [],
        {
            "run_id": "run-1",
            "model": "precomputed-model",
            "extraction_schema_version": "2.4",
            "normalization_taxonomy_version": "2.0",
        },
        document_id="version-1",
    )

    assert response["document_id"] == "version-1"
    assert response["extraction_result"]["document_id"] == "version-1"
    assert (
        response["extraction_result"]["skills"][0]["evidence"]["source_document_id"] == "version-1"
    )
    skill = response["normalized_result"]["normalized_skills"][0]
    assert skill["resolution_status"] == "resolved"
    assert skill["category_code"] == "technology"
    assert response["skill_taxonomy"]["skills"][0]["skill_id"] == "LANG_PYTHON"


def test_load_records_uses_only_success_rows_and_stable_workbook_identity(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    final = run_dir / "final"
    success = run_dir / "records" / "success"
    success.mkdir(parents=True)
    final.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": "run-1"}), encoding="utf-8")
    (final / "normalized_annotations.json").write_text(
        json.dumps(
            [
                {
                    "document_id": "cv_000002",
                    "normalized_skills": [],
                    "unresolved_items": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    (final / "match_features.jsonl").write_text("", encoding="utf-8")
    (success / "0002_cv_000002.json").write_text(
        json.dumps(
            {
                "cv_id": "cv_000002",
                "index": 2,
                "annotation": {},
                "review_flags": [],
            }
        ),
        encoding="utf-8",
    )
    workbook_path = tmp_path / "resumes.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["resume"])
    sheet.append(["first"])
    sheet.append(["second"])
    workbook.save(workbook_path)

    records = MODULE.load_records(run_dir, workbook_path)

    assert len(records) == 1
    assert records[0]["raw_text"] == "second"
    assert records[0]["source_record_id"] == "resumes.xlsx:3"


def test_position_feature_identity_is_mapped_to_main_cv_v3_contract() -> None:
    result = MODULE._position_classifications(
        "cv_000001",
        [
            {
                "document_id": "cv_000001",
                "feature_type": "role",
                "feature_id": "extraction-specific-id",
                "source_object_id": "work_001",
                "source_scope": "work_experience:work_001:position",
                "raw_text": "Backend Engineer",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "structured_values": {
                    "role_kind": "historical",
                    "classification_schema_version": "job-position-classification.v3",
                    "position_code": "BACKEND_ENGINEER",
                    "candidate_positions": [{"position_code": "BACKEND_ENGINEER", "score": 0.95}],
                    "career_level": "unspecified",
                    "leadership_scope": "none",
                    "technology_focus_codes": [],
                    "industry_context_codes": [],
                    "observed_skill_domain_codes": ["software_engineering"],
                    "confidence": 0.95,
                    "classification_status": "resolved",
                    "review_reason_codes": [],
                    "evidence_refs": ["source-1"],
                    "classification_policy_version": "position-classifier.v3.0",
                },
            }
        ],
        {
            "BACKEND_ENGINEER": {
                "position_name": "Backend Engineer",
                "family_code": "SOFTWARE_ENGINEERING",
                "family_name": "Software Engineering",
            }
        },
    )

    assert result[0]["feature_id"] == "role_work_001_position"
    assert result[0]["source_scope"] == "work_experience.position"
    assert result[0]["job_classification"]["family_name"] == "Software Engineering"


def test_summarize_failed_records_groups_external_failures(tmp_path: Path) -> None:
    failed = tmp_path / "run" / "records" / "failed"
    failed.mkdir(parents=True)
    (failed / "0002_cv_000002.json").write_text(
        json.dumps(
            {
                "cv_id": "cv_000002",
                "index": 2,
                "failed_case": {
                    "cv_id": "cv_000002",
                    "row_index": 2,
                    "error_type": "DeepSeekServerError",
                    "error_message": "server error",
                    "stage": "api_call",
                },
            }
        ),
        encoding="utf-8",
    )
    (failed / "0004_cv_000004.json").write_text(
        json.dumps(
            {
                "cv_id": "cv_000004",
                "index": 4,
                "failed_case": {
                    "cv_id": "cv_000004",
                    "row_index": 4,
                    "error_type": "DeepSeekServerError",
                    "error_message": "server error",
                    "stage": "api_call",
                },
            }
        ),
        encoding="utf-8",
    )

    summary = MODULE.summarize_failed_records(tmp_path / "run")

    assert summary["failed_precomputed_cv_count"] == 2
    assert summary["failure_reasons"] == {"DeepSeekServerError:api_call": 2}
    assert summary["failed_cases"][0]["cv_id"] == "cv_000002"
