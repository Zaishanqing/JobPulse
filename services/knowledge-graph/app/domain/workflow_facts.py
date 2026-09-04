"""Typed facts and plans used by knowledge-graph write workflows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentTextFacts:
    document_id: str
    raw_text: str
    source_credibility: float


@dataclass(frozen=True)
class QualityFacts:
    document: DocumentTextFacts
    peer_texts: tuple[str, ...]
    peer_document_ids: tuple[str, ...] = ()
    peer_cluster_keys: tuple[str | None, ...] = ()
    existing_cluster_key: str | None = None


@dataclass(frozen=True)
class QualityAssessmentPlan:
    document_id: str
    normalization_version: str
    duplicate_score: float
    copy_risk_score: float
    inflation_score: float
    effective_sample_weight: float
    duplicate_cluster_key: str | None
    duplicate_peer_document_id: str | None = None


def default_job_title(raw_text: str) -> str:
    """Preserve the existing first-line extraction rule as a domain rule."""
    return raw_text.splitlines()[0].strip()
