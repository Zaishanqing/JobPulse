from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from jobgraph_contracts.deepseek import DeepSeekClient
from jobgraph_contracts.offline_api_docs import install_offline_api_docs
from jobgraph_contracts.position_classifier import PositionClassifier

from .api.errors import APIError, application_error_spec
from .api.responses import error_response
from .api.routes import router
from .api.settings import ExtractionAPISettings
from .application.errors import ExtractionErrorCode, JDExtractionApplicationError
from .application.extraction_service import JDExtractionApplicationService


logger = logging.getLogger("jd_extraction.api")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _configured_service(
    settings: ExtractionAPISettings,
    base_url: str,
    model: str,
    api_key: str,
) -> JDExtractionApplicationService:
    client = DeepSeekClient(model=model, api_key=api_key, base_url=base_url)
    classifier = PositionClassifier(
        catalog_path=settings.position_taxonomy_path,
        model=model,
        max_attempts=settings.position_classification_max_attempts,
        client=client,
    )
    return JDExtractionApplicationService(
        model=model,
        normalization_path=settings.normalization_path,
        skill_taxonomy_path=settings.skill_taxonomy_path,
        position_taxonomy_path=settings.position_taxonomy_path,
        position_classification_max_attempts=settings.position_classification_max_attempts,
        position_classifier=classifier,
        client=client,
        extraction_provider=settings.extraction_provider,
        prompt_version=settings.prompt_version,
        algorithm_version=settings.algorithm_version,
        normalization_version=settings.normalization_version,
    )


def _request_id(request: Request) -> str:
    value = request.headers.get("X-Request-ID", "")
    return value if _REQUEST_ID_PATTERN.fullmatch(value) else uuid4().hex


def create_app(
    *,
    settings: ExtractionAPISettings | None = None,
    extraction_service: Any | None = None,
    model_service_factory: Any | None = None,
) -> FastAPI:
    settings = settings or ExtractionAPISettings.from_env()
    initialization_error: BaseException | None = None
    if extraction_service is None:
        try:
            extraction_service = JDExtractionApplicationService(
                model=settings.model,
                normalization_path=settings.normalization_path,
                skill_taxonomy_path=settings.skill_taxonomy_path,
                position_taxonomy_path=settings.position_taxonomy_path,
                position_classification_max_attempts=(
                    settings.position_classification_max_attempts
                ),
                extraction_provider=settings.extraction_provider,
                prompt_version=settings.prompt_version,
                algorithm_version=settings.algorithm_version,
                normalization_version=settings.normalization_version,
            )
        except Exception as exc:
            initialization_error = exc

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.executor = ThreadPoolExecutor(
            max_workers=settings.max_concurrency,
            thread_name_prefix="jd-http",
        )
        try:
            yield
        finally:
            app.state.executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(
        title="JD Extraction Service",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(app)
    app.state.settings = settings
    app.state.extraction_service = extraction_service
    app.state.service_initialization_error = initialization_error
    app.state.model_service_factory = model_service_factory or (
        lambda base_url, model, api_key: _configured_service(
            settings, base_url, model, api_key
        )
    )
    app.state.model_service_cache = {}

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = _request_id(request)
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > settings.max_request_bytes:
                        return error_response(
                            status_code=413,
                            error_code="request_too_large",
                            message="Request body exceeds the configured limit.",
                            request_id=request.state.request_id,
                        )
                except ValueError:
                    return error_response(
                        status_code=400,
                        error_code="invalid_request",
                        message="Invalid Content-Length header.",
                        request_id=request.state.request_id,
                    )
            body = await request.body()
            if len(body) > settings.max_request_bytes:
                return error_response(
                    status_code=413,
                    error_code="request_too_large",
                    message="Request body exceeds the configured limit.",
                    request_id=request.state.request_id,
                )

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = receive
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "request_finished request_id=%s method=%s path=%s status=%s",
            request.state.request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError):
        return error_response(
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.message,
            retryable=exc.retryable,
            request_id=request.state.request_id,
        )

    @app.exception_handler(JDExtractionApplicationError)
    async def handle_application_error(request: Request, exc: JDExtractionApplicationError):
        spec = application_error_spec(exc)
        return error_response(
            status_code=spec.status_code,
            error_code=exc.code.value,
            message=str(exc),
            retryable=spec.retryable,
            request_id=request.state.request_id,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return error_response(
            status_code=422,
            error_code=ExtractionErrorCode.INVALID_ENVELOPE.value,
            message="Request contract validation failed.",
            request_id=request.state.request_id,
        )

    @app.exception_handler(Exception)
    async def handle_unknown_error(request: Request, exc: Exception):
        logger.error(
            "request_failed request_id=%s method=%s path=%s error_type=%s",
            request.state.request_id,
            request.method,
            request.url.path,
            type(exc).__name__,
        )
        return error_response(
            status_code=500,
            error_code=ExtractionErrorCode.INTERNAL_ERROR.value,
            message="Internal server error.",
            request_id=request.state.request_id,
        )

    app.include_router(router)
    return app


app = create_app()
