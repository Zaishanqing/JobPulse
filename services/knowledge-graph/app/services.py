"""Deprecated compatibility exports.

Removal target: v2.0. New production code must import the owning domain or
infrastructure module instead. This module intentionally contains no rules,
queries, file access, persistence, or transaction handling.
"""

from app.application.errors import PublishGateError
from app.domain.policies import (
    RELATION_ALGORITHM_CONFIG,
    align_extraction,
    align_quote,
    effective_weight,
    normalize_key,
    quality_scores,
    relation_scores,
    version_diff,
)
from app.infrastructure.providers.normalization import Normalizer, normalize_salary
from app.infrastructure.sqlalchemy.fact_mappers import (
    latest_record, persist_extracted, persist_normalized,
)

__all__ = [
    "Normalizer", "PublishGateError", "RELATION_ALGORITHM_CONFIG",
    "align_extraction", "align_quote",
    "effective_weight", "latest_record",
    "normalize_key", "normalize_salary", "persist_extracted",
    "persist_normalized", "quality_scores", "relation_scores", "version_diff",
]
