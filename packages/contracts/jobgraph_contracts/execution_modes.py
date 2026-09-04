"""Execution mode identifiers and the fail-closed result policy.

`mock` is deliberately not an execution mode. A model failure must never be
relabeled as a rule, demo, shadow, or explanation success.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from jobgraph_contracts.base import StrictContract


EXECUTION_MODE_RESULT_VERSION = "execution-mode-result.v1"

ExecutionMode = Literal[
    "rule",
    "llm",
    "human_confirmed",
    "demo",
    "semantic_shadow",
    "rag_explanation",
]


EXECUTION_MODE_SEMANTICS: dict[str, dict[str, object]] = {
    "rule": {
        "label": "deterministic rule result",
        "result_fields": ("rule_based", "provider", "algorithm_version"),
        "model_required": False,
        "demo_only": False,
        "changes_official_score": True,
        "creates_business_facts": True,
    },
    "llm": {
        "label": "real model result",
        "result_fields": ("execution_mode", "provider", "model", "model_version"),
        "model_required": True,
        "demo_only": False,
        "changes_official_score": False,
        "creates_business_facts": True,
    },
    "human_confirmed": {
        "label": "human reviewed result",
        "result_fields": ("confirmed_by", "confirmed_at", "field_decisions"),
        "model_required": False,
        "demo_only": False,
        "changes_official_score": False,
        "creates_business_facts": True,
    },
    "demo": {
        "label": "fixed competition demo data",
        "result_fields": ("is_demo", "dataset_version", "mode"),
        "model_required": False,
        "demo_only": True,
        "changes_official_score": False,
        "creates_business_facts": True,
    },
    "semantic_shadow": {
        "label": "read-only semantic shadow",
        "result_fields": (
            "semantic_shadow_status",
            "semantic_shadow_score",
            "semantic_shadow_evidence",
        ),
        "model_required": True,
        "demo_only": False,
        "changes_official_score": False,
        "creates_business_facts": False,
    },
    "rag_explanation": {
        "label": "evidence-grounded RAG explanation",
        "result_fields": (
            "status",
            "answer",
            "references",
            "provider",
            "model",
            "model_version",
            "trace_id",
        ),
        "model_required": True,
        "demo_only": False,
        "changes_official_score": False,
        "creates_business_facts": False,
    },
}

MODEL_REQUIRED_MODES = frozenset({"llm", "semantic_shadow", "rag_explanation"})


class ExecutionModeResultV1(StrictContract):
    contract_version: Literal["execution-mode-result.v1"] = (
        "execution-mode-result.v1"
    )
    requested_mode: ExecutionMode
    result_mode: ExecutionMode
    status: Literal["succeeded", "failed", "available", "unavailable", "disabled"]
    error_code: str | None = None
    error_message: str | None = None
    is_demo: bool = False
    dataset_version: str | None = None

    @model_validator(mode="after")
    def validate_mode_result(self) -> "ExecutionModeResultV1":
        if (
            self.requested_mode in MODEL_REQUIRED_MODES
            and self.result_mode != self.requested_mode
        ):
            raise ValueError(
                f"{self.requested_mode} result must not fall back to another execution mode"
            )
        if self.status == "failed" and not self.error_code:
            raise ValueError("failed result requires error_code")
        if self.status in {"succeeded", "available"} and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError(
                "succeeded or available results cannot carry error fields"
            )
        if self.result_mode == "demo" and (
            not self.is_demo or not self.dataset_version
        ):
            raise ValueError("demo result requires is_demo and dataset_version")
        if self.result_mode == "semantic_shadow" and self.status not in {
            "available",
            "unavailable",
            "disabled",
        }:
            raise ValueError("semantic_shadow result must use shadow status semantics")
        if (
            self.result_mode
            in {"rule", "llm", "human_confirmed", "demo", "rag_explanation"}
            and self.status not in {"succeeded", "failed"}
        ):
            raise ValueError(
                f"{self.result_mode} result must be succeeded or failed"
            )
        return self


__all__ = [
    "EXECUTION_MODE_RESULT_VERSION",
    "EXECUTION_MODE_SEMANTICS",
    "ExecutionMode",
    "ExecutionModeResultV1",
    "MODEL_REQUIRED_MODES",
]
