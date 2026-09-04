from dataclasses import dataclass
from typing import Protocol


class CatalogEmbeddingError(RuntimeError):
    """The optional Catalog embedding dependency could not produce valid vectors."""


class CatalogEmbeddingPort(Protocol):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


@dataclass(frozen=True)
class NormalizationSuggestion:
    skill_id: str
    skill_name: str
    category: str | None
    rank: int
    lexical_score: float
    semantic_score: float | None
    combined_score: float
    matched_alias: str | None
    reasons: tuple[str, ...]
    semantic_available: bool


__all__ = [
    "CatalogEmbeddingError",
    "CatalogEmbeddingPort",
    "NormalizationSuggestion",
]
