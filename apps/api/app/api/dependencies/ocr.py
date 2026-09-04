from fastapi import Request

from app.contexts.platform import ManageOCR
from app.api.dependencies.container import get_application_container


def get_ocr_use_cases(request: Request) -> ManageOCR:
    return get_application_container(request).ocr
