"""Explicit values used by graph editing, review, and governance writes."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.value_types import ExtensionAttributes


@dataclass(frozen=True)
class SkillResolutionRequest:
    skill_id: str | None = None
    canonical_name: str | None = None
    category_code: str | None = None
    subcategory_code: str | None = None
    alias: str | None = None
    extensions: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class RelationModification:
    build_run_id: int
    position_id: str
    expected_revision: int
    reason: str
    weight: float | None = None
    confidence: float | None = None
    importance_level: str | None = None
    changed_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ReviewTaskDraft:
    object_type: str
    object_id: str
    build_run_id: int | None = None
    attributes: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewCompletion:
    action: str
    reason: str | None = None
    attributes: ExtensionAttributes = field(default_factory=dict)


@dataclass(frozen=True)
class AlgorithmConfigUpdate:
    version: str
    parameters: ExtensionAttributes
    active: bool = True


@dataclass(frozen=True)
class AlgorithmConfigResult:
    config_id: int
    version: str
