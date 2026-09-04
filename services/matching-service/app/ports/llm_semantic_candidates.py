"""Port for LLM skill candidate recall."""

from __future__ import annotations

from typing import Protocol

from app.domain.deepseek_candidates import LLMSemanticCandidateBatch
from app.domain.profiles import CVMatchProfile, PositionMatchProfile


class LLMSemanticCandidateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LLMSemanticCandidateSource(Protocol):
    def generate_candidates(
        self,
        *,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
    ) -> LLMSemanticCandidateBatch: ...


__all__ = ["LLMSemanticCandidateError", "LLMSemanticCandidateSource"]
