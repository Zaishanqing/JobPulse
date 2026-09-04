from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.run_cleaning import clean_run
from src.text_cleaning import clean_jd_text


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".jsonl":
        path.write_text(
            "\n".join(
                json.dumps(item, ensure_ascii=False)
                for item in (value if isinstance(value, list) else [value])
            ),
            encoding="utf-8",
        )
    else:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture_run(root: Path, *, include_failing: bool = False) -> Path:
    run = root / "runs" / "sample"
    raw_text = "算法工程师\n岗位职责：负责物流boss算法开发。\n来自BOSS直聘\nNone"
    audits = {
        "jdv1_ok": {
            "run_id": "sample",
            "jd_id": "jdv1_ok",
            "source_row": {"原始文本": raw_text},
            "user_prompt": "source_blocks: []",
        }
    }
    annotations = [
        {
            "document_id": "jdv1_ok",
            "job_title": {
                "value": "算法工程师",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": "算法工程师",
                    "start": 0,
                    "end": 5,
                    "alignment": "exact",
                    "occurrence_index": 0,
                },
            },
            "responsibilities": [
                {
                    "kind": "task",
                    "modality": "required",
                    "action": "负责物流boss算法开发",
                    "evidence": {
                        "source_id": "src_0002",
                        "quote": "负责物流boss算法开发",
                        "start": 5,
                        "end": 15,
                        "alignment": "exact",
                        "occurrence_index": 0,
                    },
                }
            ],
            "requirements": [],
            "company_facts": [],
            "employment_facts": [],
        },
    ]
    normalized = [
        {
            "document_id": "jdv1_ok",
            "job_classification": {"source_title": "算法工程师"},
            "normalized_requirements": [
                {"skills": [{"source_name": "物流boss算法"}]}
            ],
            "unresolved_items": [],
        }
    ]
    if include_failing:
        failing_raw_text = "数据工程师\n来自BOSS直聘\nNone"
        audits["jdv1_failed"] = {
            "run_id": "sample",
            "jd_id": "jdv1_failed",
            "source_row": {"原始文本": failing_raw_text},
            "user_prompt": "source_blocks: []",
        }
        annotations.append(
            {
            "document_id": "jdv1_failed",
            "job_title": {
                "value": "数据工程师",
                "evidence": {
                    "source_id": "src_0001",
                    "quote": "数据工程师",
                    "start": 0,
                    "end": 5,
                    "alignment": "exact",
                    "occurrence_index": 0,
                },
            },
            "responsibilities": [
                {
                    "kind": "task",
                    "modality": "required",
                    "action": "数据来自BOSS直聘官网",
                    "evidence": {
                        "source_id": "src_0002",
                        "quote": "来自BOSS直聘",
                        "start": 2,
                        "end": 8,
                        "alignment": "exact",
                        "occurrence_index": 0,
                    },
                }
            ],
            "requirements": [],
            "company_facts": [],
            "employment_facts": [],
            }
        )
        normalized.append(
            {
            "document_id": "jdv1_failed",
            "job_classification": {"source_title": "数据工程师"},
            "normalized_requirements": [],
            "unresolved_items": [],
            }
        )
    _write(run / "manifest.json", {"run_id": "sample", "source_platform": "boss_zhipin"})
    _write(run / "final" / "annotations_nested.json", annotations)
    _write(run / "final" / "normalized_annotations.json", normalized)
    _write(run / "final" / "failed_cases.jsonl", [])
    _write(run / "final" / "review_flags.jsonl", [])
    for document_id, audit in audits.items():
        _write(run / "audit" / f"000001_{document_id}.json", audit)
    return run


def test_clean_run_keeps_raw_text_and_remaps_evidence(tmp_path: Path) -> None:
    run = _fixture_run(tmp_path)

    summary = clean_run(run, tmp_path / "cleaned")

    assert summary["record_count"] == 1
    assert summary["cleaned_count"] == 1
    records = json.loads(
        (tmp_path / "cleaned" / "final" / "annotations_nested.json").read_text(
            encoding="utf-8"
        )
    )
    ok_record = next(row for row in records if row["document_id"] == "jdv1_ok")
    raw_text = "算法工程师\n岗位职责：负责物流boss算法开发。\n来自BOSS直聘\nNone"
    assert ok_record["raw_text"] == raw_text
    assert ok_record["cleaned_text"] == clean_jd_text(raw_text)
    assert ok_record["cleaning_status"] == "ok"
    evidence = ok_record["responsibilities"][0]["evidence"]
    assert evidence["quote"] == "负责物流算法开发"
    assert evidence["raw_quote"] == "负责物流boss算法开发"
    cleaned = ok_record["cleaned_text"]
    assert cleaned[evidence["start"] : evidence["end"]] == evidence["quote"]
    assert ok_record["responsibilities"][0]["action"] == "负责物流算法开发"


def test_clean_run_fails_when_any_record_cannot_be_cleaned(tmp_path: Path) -> None:
    run = _fixture_run(tmp_path, include_failing=True)

    with pytest.raises(ValueError, match="evidence remap failed"):
        clean_run(run, tmp_path / "cleaned")
    assert not (tmp_path / "cleaned").exists()


def test_clean_run_cleans_normalized_semantic_values(tmp_path: Path) -> None:
    run = _fixture_run(tmp_path)

    clean_run(run, tmp_path / "cleaned")

    normalized = json.loads(
        (tmp_path / "cleaned" / "final" / "normalized_annotations.json").read_text(
            encoding="utf-8"
        )
    )
    ok_record = next(row for row in normalized if row["document_id"] == "jdv1_ok")
    assert ok_record["normalized_requirements"][0]["skills"][0]["source_name"] == "物流算法"
    assert ok_record["cleaning_status"] == "ok"
