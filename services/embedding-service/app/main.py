from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jobgraph_contracts.offline_api_docs import install_offline_api_docs

from app.backend import (
    BgeM3Backend,
    DenseEmbeddingBackend,
    EmbeddingInferenceError,
    EmbeddingStartupError,
)
from app.config import Settings
from app.contracts import EmbeddingRequest, EmbeddingResponse, Usage

BackendLoader = Callable[[Settings], DenseEmbeddingBackend]


def create_app(
    settings: Settings | None = None,
    backend_loader: BackendLoader = BgeM3Backend,
) -> FastAPI:
    configured = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.backend = await run_in_threadpool(backend_loader, configured)
        except EmbeddingStartupError:
            raise
        app.state.semaphore = asyncio.Semaphore(configured.max_concurrency)
        app.state.ready = True
        yield
        app.state.ready = False

    app = FastAPI(
        title="Jobgraph Embedding Service",
        version="1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(app)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(item) for item in issue["loc"]) for issue in error.errors()]
        return JSONResponse(
            status_code=422,
            content={
                "code": "EMBEDDING_REQUEST_INVALID",
                "message": f"invalid fields: {', '.join(fields)}",
            },
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        is_ready = bool(getattr(request.app.state, "ready", False))
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={"status": "ready" if is_ready else "not_ready"},
        )

    @app.get("/v1/models/current")
    async def current_model() -> dict[str, object]:
        return {
            "model_id": configured.model_id,
            "model_revision": configured.model_revision,
            "dimension": configured.dimension,
            "representation": "dense",
            "similarity": "cosine",
            "normalized": True,
            "device": configured.device,
            "use_fp16": configured.use_fp16,
        }

    @app.post("/v1/embeddings", response_model=EmbeddingResponse)
    async def embeddings(
        payload: EmbeddingRequest, request: Request
    ) -> EmbeddingResponse | JSONResponse:
        if len(payload.inputs) > configured.max_input_count:
            return JSONResponse(
                status_code=422,
                content={
                    "code": "EMBEDDING_BATCH_TOO_LARGE",
                    "message": "embedding input count exceeds the configured limit",
                },
            )
        if any(len(value) > configured.max_text_chars for value in payload.inputs):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "EMBEDDING_TEXT_TOO_LONG",
                    "message": "embedding text exceeds the configured limit",
                },
            )
        started = time.perf_counter()
        try:
            async with asyncio.timeout(configured.request_timeout_seconds):
                async with request.app.state.semaphore:
                    vectors = await run_in_threadpool(
                        request.app.state.backend.encode,
                        payload.inputs,
                        normalize=payload.normalize,
                    )
        except TimeoutError:
            return JSONResponse(
                status_code=504,
                content={
                    "code": "EMBEDDING_TIMEOUT",
                    "message": "embedding request timed out",
                },
            )
        except EmbeddingInferenceError as exc:
            return JSONResponse(
                status_code=500,
                content={
                    "code": exc.code,
                    "message": "embedding model returned invalid output",
                },
            )
        if len(vectors) != len(payload.inputs) or any(
            len(vector) != configured.dimension or not all(math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            return JSONResponse(
                status_code=500,
                content={
                    "code": "EMBEDDING_OUTPUT_INVALID",
                    "message": "embedding model returned invalid output",
                },
            )
        return EmbeddingResponse(
            vectors=vectors,
            model_id=configured.model_id,
            model_revision=configured.model_revision,
            dimension=configured.dimension,
            normalized=payload.normalize,
            usage=Usage(
                input_count=len(payload.inputs),
                character_count=sum(len(value) for value in payload.inputs),
            ),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    return app


__all__ = ["create_app"]
