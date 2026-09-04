from app.integrations.contracts import EmbeddingProvider, VectorStore
from app.contexts.platform import VectorHit, VectorMetadata


class IntegrationEmbeddingGateway:
    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider
    def embed(self, text: str) -> list[float]:
        return self._provider.embed(text)
    def provider(self) -> str:
        return self._provider.status().provider


class IntegrationVectorGateway:
    def __init__(self, store: VectorStore) -> None:
        self._store = store
    def upsert(self, vector_id: str, vector: list[float], metadata: VectorMetadata) -> None:
        self._store.upsert(vector_id, vector, {"object_type": metadata.object_type, "object_id": metadata.object_id})
    def search(self, vector: list[float], top_k: int) -> list[VectorHit]:
        hits = []
        for item in self._store.search(vector, top_k):
            metadata = item.get("metadata") or {}
            hits.append(VectorHit(str(item.get("object_id", "")), float(item.get("score", 0.0)), VectorMetadata(str(metadata.get("object_type", "")), str(metadata.get("object_id", "")))))
        return hits
    def provider(self) -> str:
        return self._store.status().provider
    def persistent(self) -> bool:
        return self._store.status().persistent
