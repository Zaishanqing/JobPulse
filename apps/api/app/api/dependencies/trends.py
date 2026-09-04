from fastapi import Request

from app.contexts.market_intelligence import ManagePredictedPositions
from app.api.dependencies.container import get_application_container


def get_prediction_use_cases(request: Request) -> ManagePredictedPositions:
    return get_application_container(request).predictions
