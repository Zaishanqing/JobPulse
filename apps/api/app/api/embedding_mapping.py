from app.contexts.platform import EmbeddingResult, SimilarityResult, VectorSearchResult


def embedding_data(result: EmbeddingResult) -> dict[str, object]:
    return {"object_type": result.object_type, "object_id": result.object_id, "embedding_id": result.vector_id, "vector_id": result.vector_id, "dimension": result.dimension, "embedding_provider": result.embedding_provider, "vector_store_provider": result.vector_store_provider, "persistent": result.persistent, "implementation_status": "deterministic_local_embedding_in_memory_vector"}


def vector_search_data(result: VectorSearchResult) -> dict[str, object]:
    hits = [
        {"object_id": item.vector_id, "score": item.score, "metadata": {"object_type": item.metadata.object_type, "object_id": item.metadata.object_id}}
        for item in result.results
    ]
    return {"object_type": result.object_type, "query": result.query, "top_k": result.top_k, "results": hits, "persistent": result.persistent, "implementation_status": "deterministic_local_in_memory_vector_search"}


def similarity_data(result: SimilarityResult) -> dict[str, object]:
    return {"similarity": result.similarity, "embedding_provider": result.embedding_provider, "implementation_status": "deterministic_local_cosine_similarity"}
