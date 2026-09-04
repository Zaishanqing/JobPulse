from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from jobgraph_contracts.cv_extraction_http import CVExtractionRequest
from jobgraph_contracts.offline_api_docs import install_offline_api_docs

from src.exceptions import CVExtractorError

from .application import CVExtractionApplicationService, CVExtractionDocument
from .config import Settings


LOGGER = logging.getLogger(__name__)


def _provider_failure(exc: Exception) -> tuple[int, dict]:
    from jobgraph_contracts.deepseek import (
        DeepSeekAuthError,
        DeepSeekClientError,
        DeepSeekConnectionError,
        DeepSeekModelNotFoundError,
        DeepSeekRateLimitError,
        DeepSeekServerError,
        DeepSeekTimeoutError,
        InvalidJSONError,
        MissingAPIKeyError,
    )

    if isinstance(exc, MissingAPIKeyError):
        return 503, {
            "code": "CV_EXTRACTION_API_KEY_MISSING",
            "message": "DeepSeek API key is not configured",
        }
    if isinstance(exc, DeepSeekAuthError):
        return 502, {
            "code": "CV_EXTRACTION_AUTH_FAILED",
            "message": "DeepSeek authentication failed",
        }
    if isinstance(exc, DeepSeekModelNotFoundError):
        return 502, {
            "code": "CV_EXTRACTION_MODEL_NOT_AVAILABLE",
            "message": str(exc),
        }
    if isinstance(exc, DeepSeekRateLimitError):
        return 429, {
            "code": "CV_EXTRACTION_RATE_LIMITED",
            "message": "DeepSeek rate limit reached",
        }
    if isinstance(exc, DeepSeekTimeoutError):
        return 504, {
            "code": "CV_EXTRACTION_PROVIDER_TIMEOUT",
            "message": "DeepSeek request timed out",
        }
    if isinstance(exc, DeepSeekConnectionError):
        return 503, {
            "code": "CV_EXTRACTION_PROVIDER_CONNECTION_FAILED",
            "message": f"DeepSeek connection failed ({exc.reason})",
        }
    if isinstance(exc, InvalidJSONError):
        return 502, {
            "code": "CV_EXTRACTION_PROVIDER_INVALID_RESPONSE",
            "message": "DeepSeek returned an invalid JSON response",
        }
    if isinstance(exc, DeepSeekServerError):
        return 502, {
            "code": "CV_EXTRACTION_PROVIDER_UNAVAILABLE",
            "message": "DeepSeek provider is unavailable",
        }
    if isinstance(exc, DeepSeekClientError):
        return 502, {
            "code": "CV_EXTRACTION_PROVIDER_UNAVAILABLE",
            "message": "DeepSeek provider is unavailable",
        }
    return 503, {
        "code": "CV_EXTRACTION_PROVIDER_UNAVAILABLE",
        "message": f"CV extraction provider failed: {type(exc).__name__}",
    }


def _raise_provider_failure(exc: Exception) -> None:
    status_code, detail = _provider_failure(exc)
    LOGGER.error(
        "cv_extraction_provider_failed: %s - %s",
        detail["code"],
        detail["message"],
    )
    raise HTTPException(status_code=status_code, detail=detail) from exc


def get_service(request: Request) -> CVExtractionApplicationService:
    return request.app.state.service


def require_internal_token(
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    configured = request.app.state.settings.CV_EXTRACTION_INTERNAL_TOKEN or ""
    if not compare_digest(x_internal_token or "", configured):
        raise HTTPException(status_code=401, detail="Invalid CV extraction internal token")


def create_app(
    settings: Settings | None = None,
    service: CVExtractionApplicationService | None = None,
) -> FastAPI:
    # uvicorn 只配置自家 logger；root 没有 handler 时应用日志（pipeline 阶段、
    # 每次 LLM 调用耗时）会静默丢弃。basicConfig 在已有 handler 时是 no-op。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if service is not None and settings is None:
        raise ValueError("Explicit service injection requires explicit settings")
    configured_service = (
        service or CVExtractionApplicationService(settings)
        if settings is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # Importing the ASGI module must remain possible for image inspection.
        # The internal token and resource contracts are validated at startup.
        # LLM configuration remains an explicit per-mode readiness condition,
        # while the extraction provider is required for runtime readiness.
        runtime_settings = settings or Settings()
        application.state.settings = runtime_settings
        application.state.service = configured_service or CVExtractionApplicationService(
            runtime_settings
        )
        yield

    application = FastAPI(
        title="NFBS CV Extraction",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(application)
    if settings is not None:
        # Explicitly injected test/application settings are already validated
        # and remain available to TestClient instances that do not enter a
        # lifespan context manager.
        application.state.settings = settings
        application.state.service = configured_service

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/readiness")
    def readiness():
        return {
            "status": "ready",
            "modes": {
                "llm": {
                    "ready": application.state.settings.llm_ready,
                    "provider": "deepseek_http",
                },
            },
            "contract_versions": [
                "cv-extraction-http.v2",
                "cv-extraction-http.v3",
            ],
        }

    @application.post(
        "/api/v2/cv-extractions",
        dependencies=[Depends(require_internal_token)],
    )
    def extract_v2(
        payload: CVExtractionRequest,
        use_cases: Annotated[
            CVExtractionApplicationService,
            Depends(get_service),
        ],
    ):
        try:
            data = use_cases.extract_v2(
                CVExtractionDocument(**payload.model_dump())
            )
        except CVExtractorError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except Exception as exc:
            LOGGER.exception(
                "cv_extraction_request_failed document_id=%s contract=v2",
                payload.document_id,
            )
            _raise_provider_failure(exc)
        return {"code": 0, "message": "success", "data": data}

    @application.post(
        "/api/v3/cv-extractions",
        dependencies=[Depends(require_internal_token)],
    )
    def extract_v3(
        payload: CVExtractionRequest,
        use_cases: Annotated[
            CVExtractionApplicationService,
            Depends(get_service),
        ],
    ):
        try:
            data = use_cases.extract_v3(
                CVExtractionDocument(**payload.model_dump())
            )
        except CVExtractorError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except Exception as exc:
            LOGGER.exception(
                "cv_extraction_request_failed document_id=%s contract=v3",
                payload.document_id,
            )
            _raise_provider_failure(exc)
        return {"code": 0, "message": "success", "data": data}

    @application.get(
        "/api/v3/cv-extractions/{document_id}/progress",
        dependencies=[Depends(require_internal_token)],
    )
    def extraction_progress(
        document_id: str,
        use_cases: Annotated[
            CVExtractionApplicationService,
            Depends(get_service),
        ],
    ):
        return {
            "code": 0,
            "message": "success",
            "data": use_cases.progress_for(document_id),
        }

    return application


def build_app() -> FastAPI:
    return create_app()


app = build_app()
