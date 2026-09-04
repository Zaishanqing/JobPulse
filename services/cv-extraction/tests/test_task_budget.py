from __future__ import annotations

from pathlib import Path
from threading import Lock
from time import perf_counter

from src.deepseek_client import DeepSeekResult, DeepSeekTimeoutError
from src.pipeline import CVExtractionPipeline

NORMALIZATION_PATH = str(
    Path(__file__).resolve().parents[1]
    / "resources"
    / "normalization"
    / "2.0"
    / "normalization_map.yaml"
)

RAW_TEXT = "熟练使用 Python"
CV_INPUT = {
    "cv_id": "cv-budget",
    "source_blocks": [
        {
            "source_id": "src_0001",
            "text": RAW_TEXT,
            "start": 0,
            "end": len(RAW_TEXT),
        }
    ],
}
INITIAL_API_ATTEMPTS = (
    {
        "attempt": 1,
        "mode": "initial_section_shard",
        "elapsed_ms": 1,
        "transport_attempt_count": 1,
    },
)


class CountingClient:
    call_count = 0
    lock = Lock()

    def __init__(self, model: str, timeout: int = 300, **_: object) -> None:
        self.model = model

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        with self.lock:
            type(self).call_count += 1
        raise AssertionError("provider call reached in budget test")


def _build_pipeline(monkeypatch, task_budget_seconds: int) -> CVExtractionPipeline:
    CountingClient.call_count = 0
    monkeypatch.setattr("src.pipeline.DeepSeekClient", CountingClient)
    # 强制确定性校验失败，且不依赖 local_repair 内部行为，直接走 full_reextract
    # 重试路径，从而触发 request() 里的任务预算检查。
    monkeypatch.setattr(
        "src.pipeline.collect_payload_evidence_binding_errors",
        lambda *args, **kwargs: [{"entry_id": "skill_001", "reason": "synthetic"}],
    )
    monkeypatch.setattr("src.pipeline.plan_local_repair", lambda *args, **kwargs: None)
    return CVExtractionPipeline(
        model="fake",
        normalization_path=NORMALIZATION_PATH,
        semantic_retry_attempts=1,
        parallel_section_extraction=False,
        task_budget_seconds=task_budget_seconds,
    )


def _run_validation_retry(pipeline: CVExtractionPipeline):
    return pipeline._extract_validated(
        CV_INPUT,
        user_prompt="ignored",
        initial_result=DeepSeekResult(
            data={
                "personal_info": None,
                "education": [],
                "work_experience": [],
                "project_experience": [],
                "skills": [],
                "languages": [],
                "certificates": [],
                "awards": [],
                "publications": [],
                "patents": [],
                "research_outputs": [],
                "self_evaluation": None,
            },
            raw_response="{}",
        ),
        initial_api_attempts=INITIAL_API_ATTEMPTS,
    )


def test_retry_call_stops_when_task_budget_exhausted(monkeypatch) -> None:
    pipeline = _build_pipeline(monkeypatch, task_budget_seconds=1)
    pipeline._task_started = perf_counter() - 100

    outcome = _run_validation_retry(pipeline)

    assert isinstance(outcome.error, DeepSeekTimeoutError)
    assert "budget" in str(outcome.error)
    assert CountingClient.call_count == 0


def test_retry_call_runs_within_task_budget(monkeypatch) -> None:
    pipeline = _build_pipeline(monkeypatch, task_budget_seconds=570)
    pipeline._task_started = perf_counter()

    outcome = _run_validation_retry(pipeline)

    # 预算未耗尽时会真正发起重试调用（由 fake client 拦截并计数）。
    assert CountingClient.call_count == 1
    assert isinstance(outcome.error, AssertionError)
