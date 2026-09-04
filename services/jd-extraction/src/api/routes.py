from __future__ import annotations

import asyncio
import hashlib
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Request

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

from ..application.errors import ExtractionErrorCode, JDExtractionApplicationError
from .auth import require_internal_token
from .errors import APIError
from .responses import success_response
from .schemas import EnvelopeItemValidationError, parse_envelope_item


router = APIRouter()


def _service(request: Request):
    base_url = request.headers.get("X-JobPulse-Model-Base-URL")
    model = request.headers.get("X-JobPulse-Model-Name")
    api_key = request.headers.get("X-JobPulse-Model-API-Key")
    if any(value is not None for value in (base_url, model, api_key)):
        if not base_url or not model or not api_key or not base_url.startswith(("http://", "https://")):
            raise APIError(
                status_code=400,
                error_code="model_configuration_invalid",
                message="Model service configuration is incomplete.",
            )
        cache_key = (base_url, model, hashlib.sha256(api_key.encode("utf-8")).hexdigest())
        service = request.app.state.model_service_cache.get(cache_key)
        if service is None:
            try:
                service = request.app.state.model_service_factory(base_url, model, api_key)
            except Exception as exc:
                raise APIError(
                    status_code=503,
                    error_code="model_unavailable",
                    message="The configured model provider is unavailable.",
                    retryable=True,
                ) from exc
            request.app.state.model_service_cache[cache_key] = service
        return service
    service = request.app.state.extraction_service
    if service is None:
        raise APIError(
            status_code=503,
            error_code="service_not_ready",
            message="Extraction service dependencies are unavailable.",
            retryable=True,
        )
    return service


async def _extract_v2(request: Request, envelope: CrawlerJDEnvelopeV1):
    loop = asyncio.get_running_loop()
    service = _service(request)
    return await loop.run_in_executor(
        request.app.state.executor,
        partial(service.extract_one_v2, envelope),
    )


@router.get("/health")
async def health():
    return success_response({"status": "alive"})


@router.get("/readiness")
async def readiness(request: Request):
    errors = list(request.app.state.settings.configuration_errors)
    if request.app.state.settings.internal_token is None:
        errors.append("internal_token_missing")
    initialization_error = request.app.state.service_initialization_error
    dynamic_model_configuration = (
        isinstance(initialization_error, JDExtractionApplicationError)
        and initialization_error.code == ExtractionErrorCode.MODEL_UNAVAILABLE
        and request.app.state.model_service_factory is not None
    )
    if initialization_error is not None and not dynamic_model_configuration:
        errors.append("extraction_service_dependency_unavailable")
    ready = not errors and (
        request.app.state.extraction_service is not None or dynamic_model_configuration
    )
    if ready:
        return success_response({"status": "ready", "ready": True})
    raise APIError(
        status_code=503,
        error_code="service_not_ready",
        message="Extraction service is not ready.",
        retryable=True,
    )


@router.post("/api/v2/extractions", dependencies=[Depends(require_internal_token)])
async def extract_one_v2(payload: dict[str, Any], request: Request):
    try:
        envelope = parse_envelope_item(payload)
    except EnvelopeItemValidationError as exc:
        raise APIError(
            status_code=422,
            error_code=exc.code.value,
            message="Request contract validation failed.",
        ) from exc
    bundle = await _extract_v2(request, envelope)
    return success_response(bundle.model_dump(mode="json"))
