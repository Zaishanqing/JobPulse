"""Shared framework-free decision values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DomainRejection:
    kind: Literal["not_found", "validation", "conflict"]
    message: str
    error_code: str | None = None
