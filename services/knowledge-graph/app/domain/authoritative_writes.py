"""Rules that protect authoritative documents from legacy write paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.decisions import DomainRejection


LegacyWriteOperation = Literal[
    "jd_upsert",
    "default_extraction",
    "import_extraction",
    "confirm_extraction",
    "normalization",
    "import_normalization",
    "skill_resolution",
]


@dataclass(frozen=True)
class AuthoritativeWriteFacts:
    document_id: str
    exists: bool
    fact_authority: str | None


@dataclass(frozen=True)
class AuthoritativeWriteCommand:
    operation: LegacyWriteOperation


@dataclass(frozen=True)
class AuthoritativeWriteDecision:
    accepted: bool
    rejection: DomainRejection | None = None


def decide_authoritative_write(
    facts: AuthoritativeWriteFacts,
    command: AuthoritativeWriteCommand,
) -> AuthoritativeWriteDecision:
    if facts.fact_authority != "authoritative":
        return AuthoritativeWriteDecision(True)
    message = (
        "authoritative JD facts cannot be overwritten by legacy import"
        if command.operation == "jd_upsert"
        else "authoritative JD facts cannot be changed through a legacy endpoint"
    )
    return AuthoritativeWriteDecision(
        False,
        DomainRejection(
            "conflict", message, "AUTHORITATIVE_FACT_WRITE_PROTECTED"
        ),
    )
