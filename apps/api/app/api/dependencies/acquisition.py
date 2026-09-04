from fastapi import Request

from app.api.dependencies.container import get_application_container
from app.contexts.acquisition import AcquisitionUseCases


def get_acquisition_use_cases(request: Request) -> AcquisitionUseCases:
    use_cases = get_application_container(request).acquisition
    if use_cases is None:
        raise RuntimeError("Acquisition use cases are not configured")
    return use_cases
