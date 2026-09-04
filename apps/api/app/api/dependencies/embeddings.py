from fastapi import Request

from app.contexts.platform import ManageEmbeddings
from app.api.dependencies.container import get_application_container


def get_embedding_use_cases(request: Request) -> ManageEmbeddings:
    return get_application_container(request).embeddings
