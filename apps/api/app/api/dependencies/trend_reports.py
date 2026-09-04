from fastapi import Request

from app.contexts.market_intelligence import ManageTrendReports
from app.api.dependencies.container import get_application_container


def get_trend_report_use_cases(request: Request) -> ManageTrendReports:
    return get_application_container(request).trend_reports
