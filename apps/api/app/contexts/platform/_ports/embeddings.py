from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmbeddingResult:
    object_type: str
    object_id: str
    vector_id: str
    dimension: int
    embedding_provider: str
    vector_store_provider: str
    persistent: bool


@dataclass(frozen=True)
class VectorSearchResult:
    object_type: str
    query: str
    top_k: int
    results: tuple["VectorHit", ...]
    persistent: bool


@dataclass(frozen=True)
class VectorMetadata:
    object_type: str
    object_id: str


@dataclass(frozen=True)
class VectorHit:
    vector_id: str
    score: float
    metadata: VectorMetadata


@dataclass(frozen=True)
class SimilarityResult:
    similarity: float
    embedding_provider: str


class EmbeddingSourcePort(Protocol):
    def get_text(self, object_id: str) -> str | None: ...


class EmbeddingGatewayPort(Protocol):
    def embed(self, text: str) -> list[float]: ...
    def provider(self) -> str: ...


class VectorGatewayPort(Protocol):
    def upsert(self, vector_id: str, vector: list[float], metadata: VectorMetadata) -> None: ...
    def search(self, vector: list[float], top_k: int) -> list[VectorHit]: ...
    def provider(self) -> str: ...
    def persistent(self) -> bool: ...
