from collections.abc import Callable
from typing import Protocol, TypeVar

from app.integrations.base import CapabilityStatus


T = TypeVar("T")


class CapabilityAdapter(Protocol):
    def status(self) -> CapabilityStatus: ...


class LLMProvider(CapabilityAdapter, Protocol):
    def generate(self, prompt: str) -> str: ...


class OCRProvider(CapabilityAdapter, Protocol):
    def extract_text(self, content: bytes, content_type: str) -> str: ...


class DocumentParser(CapabilityAdapter, Protocol):
    def extract_text(self, content: bytes, content_type: str) -> str: ...


class EmbeddingProvider(CapabilityAdapter, Protocol):
    def embed(self, text: str) -> list[float]: ...


class VectorStore(CapabilityAdapter, Protocol):
    def upsert(self, object_id: str, vector: list[float], metadata: dict | None = None) -> None: ...
    def search(self, vector: list[float], top_k: int = 10) -> list[dict]: ...


class TaskQueue(CapabilityAdapter, Protocol):
    def execute(self, task_type: str, payload: dict, handler: Callable[[dict], T]) -> T: ...


class FileStorage(CapabilityAdapter, Protocol):
    def save(self, key: str, content: bytes) -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class EvidenceRetriever(CapabilityAdapter, Protocol):
    def retrieve(self, query: str, documents: list[dict], top_k: int = 5) -> list[dict]: ...


class TrendSourceCrawler(CapabilityAdapter, Protocol):
    def fetch(self, source: dict) -> list[dict]: ...
