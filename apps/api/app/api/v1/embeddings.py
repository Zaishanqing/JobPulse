from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.embeddings import get_embedding_use_cases
from app.api.embedding_mapping import embedding_data, similarity_data, vector_search_data
from app.contexts.platform import EmbeddingSourceNotFound, ManageEmbeddings
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.embeddings import EmbeddingRuleViolation
from app.domain.errors import PermissionDenied
from app.schemas.api_requests import EmbeddingBatchRequest, SimilarityRequest, VectorSearchRequest


router = APIRouter(tags=["embeddings"])


def _raise(exc: Exception) -> None:
    if isinstance(exc, EmbeddingSourceNotFound):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    else:
        code = 422
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _generate(
    object_type: str, object_id: str, actor: AccountActor, use_cases: ManageEmbeddings
) -> dict[str, object]:
    try:
        result, task = use_cases.generate(actor, object_type, object_id)
    except (EmbeddingSourceNotFound, EmbeddingRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return {**embedding_data(result), "task_id": task.task_id, "canonical_status": task.status}


@router.post("/embeddings/jds/batch")
def generate_jd_embeddings_batch(
    payload: EmbeddingBatchRequest = Body(default_factory=lambda: EmbeddingBatchRequest([])),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return success_response(
        data={"items": [_generate("jd", jd_id, actor, use_cases) for jd_id in payload.root]}
    )


@router.post("/embeddings/jds/{jd_id}")
def generate_jd_embedding(
    jd_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return success_response(data=_generate("jd", jd_id, actor, use_cases))


@router.post("/embeddings/resumes/{resume_id}")
def generate_resume_embedding(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return success_response(data=_generate("resume", resume_id, actor, use_cases))


@router.post("/embeddings/skills/{skill_id}")
def generate_skill_embedding(
    skill_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return success_response(data=_generate("skill", skill_id, actor, use_cases))


@router.post("/embeddings/positions/{position_id}")
def generate_position_embedding(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return success_response(data=_generate("position", position_id, actor, use_cases))


@router.post("/embeddings/evidence/{evidence_id}")
def generate_evidence_embedding(
    evidence_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return success_response(data=_generate("evidence", evidence_id, actor, use_cases))


@router.post("/embeddings/relations/{relation_id}")
def generate_relation_embedding(
    relation_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return success_response(data=_generate("relation", relation_id, actor, use_cases))


def _search(object_type: str, payload: dict, actor: AccountActor, use_cases: ManageEmbeddings):
    try:
        result = use_cases.search(actor, object_type, payload)
    except (EmbeddingRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=vector_search_data(result))


def _add_search_route(path: str, object_type: str) -> None:
    def endpoint(
        payload: VectorSearchRequest = Body(default_factory=lambda: VectorSearchRequest({})),
        actor: AccountActor = Depends(get_account_actor),
        use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
    ):
        return _search(object_type, payload.root, actor, use_cases)

    endpoint.__name__ = f"search_{object_type}_vectors"
    router.add_api_route(f"/vectors/search/{path}", endpoint, methods=["POST"])


for _path, _type in (
    ("jds", "jd"),
    ("positions", "position"),
    ("skills", "skill"),
    ("resumes", "resume"),
    ("evidence", "evidence"),
):
    _add_search_route(_path, _type)


def _similarity(payload: dict, actor: AccountActor, use_cases: ManageEmbeddings):
    try:
        result = use_cases.similarity(actor, payload)
    except (EmbeddingRuleViolation, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=similarity_data(result))


@router.post("/vectors/similarity/skill-combo")
def calculate_skill_combo_similarity(
    payload: SimilarityRequest = Body(default_factory=lambda: SimilarityRequest({})),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return _similarity(payload.root, actor, use_cases)


@router.post("/vectors/similarity/position-relation")
def calculate_position_relation_similarity(
    payload: SimilarityRequest = Body(default_factory=lambda: SimilarityRequest({})),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageEmbeddings = Depends(get_embedding_use_cases),
):
    return _similarity(payload.root, actor, use_cases)
