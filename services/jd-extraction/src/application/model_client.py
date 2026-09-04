from __future__ import annotations

from typing import Protocol

from ..deepseek_client import DeepSeekResult


class JDModelClient(Protocol):
    """Minimal model boundary injected by the application service."""

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        ...
