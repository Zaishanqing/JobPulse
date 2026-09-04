from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pandas as pd

from application_fakes import valid_payload
from src.audit import RunAudit
from src.deepseek_client import DeepSeekResult
from src.pipeline import JDExtractionPipeline
from src.report_generator import summarize_run


def test_run_audit_resumes_only_paired_annotation_normalized_rows() -> None:
    work = Path("pytest_artifacts") / f"resume_audit_{uuid4().hex}"
    run_dir = work / "output" / "runs" / "resume"
    try:
        final = run_dir / "final"
        final.mkdir(parents=True)
        (final / "annotations.jsonl").write_text(
            json.dumps({"document_id": "jd_1"}, ensure_ascii=False) + "\n"
            + json.dumps({"document_id": "jd_2"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (final / "normalized_annotations.jsonl").write_text(
            json.dumps({"document_id": "jd_1"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        audit = RunAudit(work / "output", "resume")
        assert audit.resume_jd_ids == {"jd_1"}
        assert run_dir.exists()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_pipeline_resumes_completed_rows_without_reextracting(monkeypatch) -> None:
    class CountingClient:
        def __init__(self, model: str) -> None:
            self.model = model
            self.calls = 0

        def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
            self.calls += 1
            payload = valid_payload()
            return DeepSeekResult(
                data=payload,
                raw_response=json.dumps(payload, ensure_ascii=False),
            )

    client = CountingClient("fake")
    monkeypatch.setattr("src.pipeline.DeepSeekClient", lambda model: client)
    work = Path("pytest_artifacts") / f"resume_pipeline_{uuid4().hex}"
    try:
        work.mkdir(parents=True)
        input_path = work / "input.csv"
        pd.DataFrame(
            [
                {"jd_id": "jd_a", "原始文本": "熟练使用 Python"},
                {"jd_id": "jd_b", "原始文本": "熟练使用 Python"},
            ]
        ).to_csv(input_path, index=False)
        kwargs = dict(
            model="fake",
            normalization_path="config/normalization_map.yaml",
            continue_on_error=False,
            run_id="resume-run",
            audit_sample_rate=0,
            max_workers=1,
            source_platform="test",
            semantic_retry_attempts=0,
        )
        pipeline = JDExtractionPipeline(**kwargs)
        pipeline.run(str(input_path), str(work / "output"))
        run = work / "output" / "runs" / "resume-run"
        first_manifest = json.loads(
            (run / "manifest.json").read_text(encoding="utf-8")
        )
        assert first_manifest["success_count"] == 2
        assert client.calls == 2

        client.calls = 0
        resumed = JDExtractionPipeline(**kwargs)
        resumed.run(str(input_path), str(work / "output"))
        second_manifest = json.loads(
            (run / "manifest.json").read_text(encoding="utf-8")
        )
        assert client.calls == 0
        assert second_manifest["resume_skipped_count"] == 2
        assert second_manifest["success_count"] == 2
        annotations = (
            run / "final" / "annotations.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        assert len(annotations) == 2
        normalized = (
            run / "final" / "normalized_annotations.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        assert len(normalized) == 2
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_interrupted_run_reextracts_rows_without_normalized_pair_and_exports(
    monkeypatch,
) -> None:
    class CountingClient:
        def __init__(self, model: str) -> None:
            self.model = model
            self.calls = 0

        def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
            self.calls += 1
            payload = valid_payload()
            return DeepSeekResult(
                data=payload,
                raw_response=json.dumps(payload, ensure_ascii=False),
            )

    client = CountingClient("fake")
    monkeypatch.setattr("src.pipeline.DeepSeekClient", lambda model: client)
    work = Path("pytest_artifacts") / f"interrupted_resume_{uuid4().hex}"
    try:
        work.mkdir(parents=True)
        input_path = work / "input.csv"
        pd.DataFrame(
            [
                {"jd_id": "jd_a", "原始文本": "熟练使用 Python"},
                {"jd_id": "jd_b", "原始文本": "熟练使用 Python"},
            ]
        ).to_csv(input_path, index=False)
        kwargs = dict(
            model="fake",
            normalization_path="config/normalization_map.yaml",
            continue_on_error=False,
            run_id="resume-run",
            audit_sample_rate=0,
            max_workers=1,
            source_platform="test",
            semantic_retry_attempts=0,
        )
        pipeline = JDExtractionPipeline(**kwargs)
        pipeline.run(str(input_path), str(work / "output"))
        run = work / "output" / "runs" / "resume-run"

        # Simulate an interrupted run: an annotation exists but its normalized
        # payload was never persisted, and a success record would skip it.
        orphan_id = "test:input.csv:row:3:3"
        with (run / "final" / "annotations.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps({"document_id": orphan_id}) + "\n")
        success = run / "records" / "success"
        (success / "000003_orphan.json").write_text(
            json.dumps(
                {"run_id": "resume-run", "jd_id": orphan_id, "status": "success"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        pd.DataFrame(
            [
                {"jd_id": "jd_a", "原始文本": "熟练使用 Python"},
                {"jd_id": "jd_b", "原始文本": "熟练使用 Python"},
                {"jd_id": "jd_c", "原始文本": "熟练使用 Python"},
            ]
        ).to_csv(input_path, index=False)
        client.calls = 0
        resumed = JDExtractionPipeline(**kwargs)
        resumed.run(str(input_path), str(work / "output"))
        manifest = json.loads(
            (run / "manifest.json").read_text(encoding="utf-8")
        )
        assert client.calls == 1
        assert manifest["success_count"] == 3
        assert manifest["resume_skipped_count"] == 2
        assert (run / "final" / "annotations.xlsx").is_file()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_resume_replaces_historical_failure_with_terminal_success(monkeypatch) -> None:
    class RecoveringClient:
        def __init__(self) -> None:
            self.calls = 0
            self.fail_next = True

        def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
            self.calls += 1
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("synthetic first-attempt failure")
            payload = valid_payload()
            return DeepSeekResult(
                data=payload,
                raw_response=json.dumps(payload, ensure_ascii=False),
            )

    client = RecoveringClient()
    monkeypatch.setattr("src.pipeline.DeepSeekClient", lambda model: client)
    work = Path("pytest_artifacts") / f"terminal_resume_{uuid4().hex}"
    try:
        work.mkdir(parents=True)
        input_path = work / "input.csv"
        pd.DataFrame(
            [
                {"jd_id": "jd_a", "原始文本": "熟练使用 Python"},
                {"jd_id": "jd_b", "原始文本": "熟练使用 Python"},
            ]
        ).to_csv(input_path, index=False)
        kwargs = dict(
            model="fake",
            normalization_path="config/normalization_map.yaml",
            continue_on_error=True,
            run_id="terminal-run",
            audit_sample_rate=0,
            max_workers=1,
            source_platform="test",
            semantic_retry_attempts=0,
        )

        JDExtractionPipeline(**kwargs).run(str(input_path), str(work / "output"))
        run = work / "output" / "runs" / "terminal-run"
        first_manifest = json.loads(
            (run / "manifest.json").read_text(encoding="utf-8")
        )
        assert first_manifest["success_count"] == 1
        assert first_manifest["failed_count"] == 1

        JDExtractionPipeline(**kwargs).run(str(input_path), str(work / "output"))
        summary = summarize_run(run)
        assert summary["manifest"]["success_count"] == 2
        assert summary["manifest"]["failed_count"] == 0
        assert summary["counts"]["annotations"] == 2
        assert summary["failed_cases"] == []
        assert all(summary["integrity_checks"].values())
        assert not any((run / "records" / "failed").glob("*.json"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
