from __future__ import annotations

import json
from pathlib import Path

from scripts.reclassify_job_positions import _refresh_run_metadata_and_report
from src.report_generator import REPORT_GENERATOR_VERSION
from src.validator import BUSINESS_VALIDATOR_VERSION


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def test_refresh_updates_position_flags_manifest_and_report(tmp_path: Path) -> None:
    run = tmp_path / "sample_position_v3"
    final = run / "final"
    final.mkdir(parents=True)
    annotations = [
        {
            "document_id": "jd_resolved",
            "responsibilities": [],
            "requirements": [],
            "company_facts": [],
            "employment_facts": [],
        },
        {
            "document_id": "jd_ambiguous",
            "responsibilities": [],
            "requirements": [],
            "company_facts": [],
            "employment_facts": [],
        },
    ]
    normalized = [
        {
            "document_id": "jd_resolved",
            "normalized_requirements": [],
            "unresolved_items": [],
            "job_classification": {
                "schema_version": "job-position-classification.v3",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "classification_status": "resolved",
                "position_code": "SOFTWARE_BACKEND_ENGINEER",
                "review_reason_codes": [],
            },
        },
        {
            "document_id": "jd_ambiguous",
            "normalized_requirements": [],
            "unresolved_items": [],
            "job_classification": {
                "schema_version": "job-position-classification.v3",
                "taxonomy_version": "position-taxonomy.v3.0.0",
                "classification_status": "ambiguous",
                "position_code": None,
                "review_reason_codes": ["close_candidates"],
            },
        },
    ]
    _write_json(
        run / "manifest.json",
        {
            "total_rows": 2,
            "success_count": 2,
            "failed_count": 0,
            "review_flag_count": 99,
            "api_call_count": 0,
            "business_validator_version": BUSINESS_VALIDATOR_VERSION,
            "report_generator_version": REPORT_GENERATOR_VERSION,
        },
    )
    _write_json(final / "normalized_annotations.json", normalized)
    _write_jsonl(final / "annotations.jsonl", annotations)
    _write_jsonl(final / "normalized_annotations.jsonl", normalized)
    _write_jsonl(
        final / "review_flags.jsonl",
        [
            {
                "document_id": "jd_ambiguous",
                "issue_type": "job_classification_not_resolved",
                "severity": "blocking",
                "details": {"classification_status": "ambiguous"},
            }
        ],
    )
    _write_jsonl(final / "failed_cases.jsonl", [])
    _write_jsonl(final / "illegal_enum_cases.jsonl", [])
    _write_jsonl(run / "logs.jsonl", [])

    counts = _refresh_run_metadata_and_report(
        run,
        catalog_version="position-taxonomy.v3.0.0",
        model="deepseek-v4-flash",
        applied_at="2026-08-09T00:00:00+00:00",
    )

    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    flags = [
        json.loads(line)
        for line in (final / "review_flags.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    report = (run / "research_report.md").read_text(encoding="utf-8")
    assert counts == {"resolved": 1, "ambiguous": 1}
    assert manifest["review_flag_count"] == 1
    assert Path(manifest["research_report_path"]) == run / "research_report.md"
    assert flags[0]["jd_id"] == "jd_ambiguous"
    assert flags[0]["rule_scope"] == "document"
    assert flags[0]["review_reason_codes"] == ["close_candidates"]
    assert "| resolved | 1 |" in report
    assert "| ambiguous | 1 |" in report
    assert "| review_flags_match_manifest | PASS |" in report
