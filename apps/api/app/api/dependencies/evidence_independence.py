from __future__ import annotations

from app.contexts.evidence_independence.contracts import ConclusionRecomputePort
from app.contexts.evidence_independence.emerging import load_emerging_conclusion_provider
from app.core.config import settings


def get_emerging_conclusion_provider(
    emerging_id: str,
    release_id: str | None = None,
) -> ConclusionRecomputePort | None:
    return load_emerging_conclusion_provider(
        emerging_id,
        release_id,
        settings.EMERGING_CONCLUSION_MANIFEST_PATH,
    )


__all__ = ["get_emerging_conclusion_provider"]
