from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

import pytest

from src.checkpoints import (
    SQLiteExtractionCheckpointStore,
    build_checkpoint_key,
    build_content_checkpoint_key,
)
from src.deepseek_client import DeepSeekResult
from src.pipeline import CVExtractionPipeline


class ResumeShardClient:
    call_count = 0
    failed_once = False
    lock = Lock()

    def __init__(self, model: str, timeout: int = 300, **_: object) -> None:
        self.model = model

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        fields = json.loads(
            user_prompt.split("requested_top_level_fields: ", 1)[1].splitlines()[0]
        )
        with self.lock:
            type(self).call_count += 1
            should_fail = "project_experience" in fields and not type(self).failed_once
            if should_fail:
                type(self).failed_once = True
        if should_fail:
            raise RuntimeError("transient project shard failure")
        payload = {
            field: None if field == "personal_info" else []
            for field in fields
        }
        return DeepSeekResult(
            data=payload,
            raw_response=json.dumps(payload, ensure_ascii=False),
        )


def test_checkpoint_key_changes_with_input_and_runtime() -> None:
    base = build_checkpoint_key(
        document_id="doc-1",
        raw_text="Python",
        runtime_fingerprint={"model": "model-a"},
    )
    assert base == build_checkpoint_key(
        document_id="doc-1",
        raw_text="Python",
        runtime_fingerprint={"model": "model-a"},
    )
    assert base != build_checkpoint_key(
        document_id="doc-1",
        raw_text="Java",
        runtime_fingerprint={"model": "model-a"},
    )
    assert base != build_checkpoint_key(
        document_id="doc-1",
        raw_text="Python",
        runtime_fingerprint={"model": "model-b"},
    )


def test_content_checkpoint_key_reuses_same_text_across_documents() -> None:
    first = build_content_checkpoint_key(
        raw_text="Python", runtime_fingerprint={"model": "model-a"}
    )
    second = build_content_checkpoint_key(
        raw_text="Python", runtime_fingerprint={"model": "model-a"}
    )
    assert first == second
    assert first != build_content_checkpoint_key(
        raw_text="Java", runtime_fingerprint={"model": "model-a"}
    )


def test_section_retry_reuses_completed_shards(monkeypatch, tmp_path) -> None:
    ResumeShardClient.call_count = 0
    ResumeShardClient.failed_once = False
    monkeypatch.setattr("src.pipeline.DeepSeekClient", ResumeShardClient)
    store = SQLiteExtractionCheckpointStore(tmp_path / "checkpoints.sqlite3")
    fingerprint = {"model": "fake", "prompt_version": "test"}
    kwargs = {
        "model": "fake",
        "normalization_path": str(
            Path(__file__).resolve().parents[1]
            / "resources"
            / "normalization"
            / "2.0"
            / "normalization_map.yaml"
        ),
        "semantic_retry_attempts": 0,
        "parallel_section_extraction": True,
        "checkpoint_store": store,
        "checkpoint_fingerprint": fingerprint,
    }
    document_id = "cv-resume"
    raw_text = "熟练使用 Python"
    cv_input = {
        "cv_id": document_id,
        "source_blocks": [
            {
                "source_id": "src_0001",
                "text": raw_text,
                "start": 0,
                "end": len(raw_text),
            }
        ],
    }
    checkpoint_key = build_content_checkpoint_key(
        raw_text=raw_text,
        runtime_fingerprint=fingerprint,
    )

    first = CVExtractionPipeline(**kwargs)
    with pytest.raises(RuntimeError, match="transient project shard failure"):
        first._extract_section_shards(
            cv_input,
            [],
            [],
            checkpoint_key=checkpoint_key,
        )

    second = CVExtractionPipeline(**kwargs)
    result, attempts = second._extract_section_shards(
        cv_input,
        [],
        [],
        checkpoint_key=checkpoint_key,
    )

    assert ResumeShardClient.call_count == 5
    assert set(result.data) == {
        "personal_info",
        "education",
        "work_experience",
        "project_experience",
        "skills",
        "languages",
        "certificates",
        "awards",
        "publications",
        "patents",
        "research_outputs",
        "self_evaluation",
    }
    assert sum(bool(item.get("checkpoint_hit")) for item in attempts) == 3
