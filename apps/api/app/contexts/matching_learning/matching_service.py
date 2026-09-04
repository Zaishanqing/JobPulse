from collections.abc import Mapping

from app.contexts.matching_learning._ports.matching import MatchingServiceReferenceRecord
from app.contexts.matching_learning._ports.matching_service import (
    MatchingIdentity,
    MatchingIdentityPort,
    MatchingServiceError,
    MatchingServicePort,
    RemoteEvaluation,
    RemoteLearningPath,
    RemoteTask,
)

__all__ = [
    "MatchingIdentity",
    "MatchingIdentityPort",
    "MatchingServiceError",
    "MatchingServicePort",
    "MatchingServiceReferenceRecord",
    "RemoteEvaluation",
    "RemoteLearningPath",
    "RemoteTask",
    "product_matching_method",
]


def product_matching_method(evaluation: object) -> str:
    """Return a stable product-level matching mode from an evaluation payload.

    ``rule`` means the responsibility dimension was produced by deterministic
    rules. ``semantic_verified`` means at least one responsibility result
    carries the formal CE chain (retrieval score, CE score or top candidates).
    Internal model/version names are intentionally not part of this value.
    """

    if not isinstance(evaluation, Mapping):
        return "rule"
    results = evaluation.get("responsibility_results")
    if not isinstance(results, (list, tuple)):
        return "rule"
    semantic_verified = any(
        isinstance(item, Mapping)
        and (
            item.get("ce_score") is not None
            or item.get("retrieval_score") is not None
            or bool(item.get("top_candidates"))
        )
        for item in results
    )
    return "semantic_verified" if semantic_verified else "rule"
