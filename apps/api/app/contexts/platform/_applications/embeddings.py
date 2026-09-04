from app.domain.json_types import FrozenJsonObject
import json
from dataclasses import dataclass
from typing import Mapping

from app.domain.accounts import AccountActor
from app.domain.embeddings import EmbeddingRuleViolation, cosine_similarity
from app.contexts.platform._ports.embeddings import EmbeddingGatewayPort, EmbeddingResult, EmbeddingSourcePort, SimilarityResult, VectorGatewayPort, VectorMetadata, VectorSearchResult
from app.contexts.tasks import TaskPayload, TaskRecord, TaskWorkflowPort
from app.domain.errors import PermissionDenied


class EmbeddingSourceNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManageEmbeddings:
    sources: Mapping[str, EmbeddingSourcePort]
    embeddings: EmbeddingGatewayPort
    vectors: VectorGatewayPort
    tasks: TaskWorkflowPort

    def generate(self, actor: AccountActor, object_type: str, object_id: str) -> tuple[EmbeddingResult, TaskRecord]:
        self._require_internal(actor)
        source = self.sources.get(object_type)
        if source is None:
            raise EmbeddingRuleViolation("Unsupported embedding object type")
        text = source.get_text(object_id)
        if text is None:
            raise EmbeddingSourceNotFound(f"{object_type.capitalize()} embedding source not found")
        if not text.strip():
            raise EmbeddingRuleViolation("Embedding source text is empty")
        vector = self.embeddings.embed(text)
        vector_id = f"{object_type}:{object_id}"
        self.vectors.upsert(vector_id, vector, VectorMetadata(object_type, object_id))
        result = EmbeddingResult(object_type, object_id, vector_id, len(vector), self.embeddings.provider(), self.vectors.provider(), self.vectors.persistent())
        task = self.tasks.create_succeeded(actor, "embedding", input_payload=TaskPayload.from_mapping({"object_type": object_type, "object_id": object_id}), result_payload=TaskPayload.from_mapping({**_embedding_task_payload(result), "mock": False}))
        return result, task

    def search(self, actor: AccountActor, object_type: str, payload: FrozenJsonObject) -> VectorSearchResult:
        if object_type == "resume":
            if actor.role not in {"enterprise_user", "admin", "developer"}:
                raise PermissionDenied("Permission denied")
        else:
            self._require_internal(actor)
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise EmbeddingRuleViolation("query must be a non-empty string")
        top_k = payload.get("top_k", 10)
        if not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise EmbeddingRuleViolation("top_k must be an integer between 1 and 100")
        raw = self.vectors.search(self.embeddings.embed(query), 100)
        results = tuple(item for item in raw if item.metadata.object_type == object_type)[:top_k]
        return VectorSearchResult(object_type, query, top_k, results, self.vectors.persistent())

    def similarity(self, actor: AccountActor, payload: FrozenJsonObject) -> SimilarityResult:
        self._require_internal(actor)
        left = self._input(payload, ("left", "source", "skills_a", "position_a"))
        right = self._input(payload, ("right", "target", "skills_b", "position_b"))
        if left is None or right is None:
            raise EmbeddingRuleViolation("Two similarity inputs are required")
        score = cosine_similarity(self.embeddings.embed(self._text(left)), self.embeddings.embed(self._text(right)))
        return SimilarityResult(round(score, 8), self.embeddings.provider())

    @staticmethod
    def _require_internal(actor: AccountActor) -> None:
        if actor.role not in {"admin", "developer"}:
            raise PermissionDenied("Permission denied")

    @staticmethod
    def _input(payload: FrozenJsonObject, names: tuple[str, ...]) -> object:
        return next((payload[name] for name in names if name in payload), None)

    @staticmethod
    def _text(value: object) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _embedding_task_payload(result: EmbeddingResult) -> FrozenJsonObject:
    return {"object_type": result.object_type, "object_id": result.object_id, "embedding_id": result.vector_id, "vector_id": result.vector_id, "dimension": result.dimension, "embedding_provider": result.embedding_provider, "vector_store_provider": result.vector_store_provider, "persistent": result.persistent, "implementation_status": "deterministic_local_embedding_in_memory_vector"}
