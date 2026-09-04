from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.trend_reports import get_trend_report_use_cases
from app.api.dependencies.container import get_application_container
from app.api.trend_delivery_mapping import collection_data, trend_task_data
from app.application_container import ApplicationContainer
from app.api.trend_report_mapping import (
    combo_data,
    combo_from_data,
    distribution_data,
    distribution_from_data,
    graph_data,
    graph_from_data,
    replacement_data,
    replacement_from_data,
    risk_data,
    risk_from_data,
    skill_data,
    skill_from_data,
    trend_report_data,
)
from app.contexts.tasks import TaskNotFound
from app.contexts.market_intelligence import (
    ManageTrendReports,
    TrendPositionNotFound,
    TrendReportNotFound,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.trend_analysis import TrendRuleViolation
from app.contexts.market_intelligence import TrendReportChanges, TrendReportRecord
from app.schemas.trend_delivery import (
    TrendBatchQuery,
    TrendDeliveryCollectionEnvelope,
    TrendDeliveryEnvelope,
)
from app.schemas.trend_change import CreateTrendChangeAnalysisRequest, CreateTrendChangeFromHistoryRequest
from app.schemas.trend_report import TrendAnalysisTaskRequest, TrendReportUpdate
from app.domain.errors import PermissionDenied


router = APIRouter(tags=["trend-reports"])
SEQUENCE_FIELDS = frozenset({"new_skills", "rising_skills", "declining_skills", "replaced_skills", "skill_combo_shifts", "risks"})


def _raise(exc: Exception) -> None:
    if isinstance(exc, (TrendPositionNotFound, TrendReportNotFound, TaskNotFound)):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    elif isinstance(exc, TrendRuleViolation):
        code = 400
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _changes(payload: TrendReportUpdate) -> TrendReportChanges:
    raw = payload.model_dump(exclude_unset=True, exclude={"reason"})
    converters = {
        "current_graph": graph_from_data,
        "skill_weight_distribution": distribution_from_data,
        "new_skills": lambda values: tuple(skill_from_data(item) for item in values),
        "rising_skills": lambda values: tuple(skill_from_data(item) for item in values),
        "declining_skills": lambda values: tuple(skill_from_data(item) for item in values),
        "replaced_skills": lambda values: tuple(replacement_from_data(item) for item in values),
        "skill_combo_shifts": lambda values: tuple(combo_from_data(item) for item in values),
        "risks": lambda values: tuple(risk_from_data(item) for item in values),
    }
    values = {name: converters[name](value) if value is not None and name in converters else value for name, value in raw.items()}
    return TrendReportChanges(frozenset(raw), **values)


def _get(report_id: str, use_cases: ManageTrendReports, actor: AccountActor) -> TrendReportRecord:
    try:
        return use_cases.get(report_id, actor)
    except (TrendReportNotFound, PermissionDenied) as exc:
        _raise(exc)


@router.post(
    "/positions/{position_id}/trend-analysis/tasks",
    response_model=TrendDeliveryEnvelope,
)
def create_trend_analysis_task(position_id: str, payload: TrendAnalysisTaskRequest | None = None, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    payload = payload or TrendAnalysisTaskRequest()
    try:
        task = use_cases.analyze(actor, position_id, payload.time_window_start, payload.time_window_end)
    except (PermissionDenied, TrendPositionNotFound, TrendRuleViolation) as exc:
        _raise(exc)
    return success_response(data=trend_task_data(task, "position_skill_trend_run"))


@router.post(
    "/trend-analysis/tasks/batch-query",
    response_model=TrendDeliveryCollectionEnvelope,
)
def batch_query_trend_analysis_tasks(payload: TrendBatchQuery, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    items = []
    missing = []
    for task_id in dict.fromkeys(payload.ids):
        try:
            items.append(trend_task_data(use_cases.task(actor, task_id), "position_skill_trend_run"))
        except TaskNotFound:
            missing.append(task_id)
        except PermissionDenied as exc:
            _raise(exc)
    return success_response(data=collection_data(
        items, page=1, page_size=max(len(items), 1), filters={"ids": payload.ids},
        sort_by="input_order", sort_order="asc", not_found_ids=missing,
    ))


@router.get("/trend-analysis/tasks/{task_id}", response_model=TrendDeliveryEnvelope)
def get_trend_analysis_task(task_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    try:
        task = use_cases.task(actor, task_id)
    except (PermissionDenied, TaskNotFound) as exc:
        _raise(exc)
    return success_response(data=trend_task_data(task, "position_skill_trend_run"))


@router.get("/trend-runs", response_model=TrendDeliveryCollectionEnvelope)
def list_trend_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    run_type: Literal["prediction_run", "position_skill_trend_run"] | None = None,
    sort_order: Literal["asc", "desc"] = "desc",
    actor: AccountActor = Depends(get_account_actor),
    container: ApplicationContainer = Depends(get_application_container),
):
    try:
        records = container.tasks.list(actor)
    except PermissionDenied as exc:
        _raise(exc)
    resource_types = {
        "predicted_position_analysis": "prediction_run",
        "trend_analysis": "position_skill_trend_run",
    }
    values = [
        trend_task_data(record, resource_types[record.task_type])
        for record in records if record.task_type in resource_types
    ]
    if status:
        values = [item for item in values if item["canonical_status"] == status]
    if run_type:
        values = [item for item in values if item["resource_type"] == run_type]
    values.sort(key=lambda item: item.get("created_at") or "", reverse=sort_order == "desc")
    return success_response(data=collection_data(
        values, page=page, page_size=page_size,
        filters={"status": status, "run_type": run_type},
        sort_by="created_at", sort_order=sort_order,
    ))


@router.post("/trend-runs/batch-query", response_model=TrendDeliveryCollectionEnvelope)
def batch_query_trend_runs(payload: TrendBatchQuery, actor: AccountActor = Depends(get_account_actor), container: ApplicationContainer = Depends(get_application_container)):
    items = []
    missing = []
    resource_types = {
        "predicted_position_analysis": "prediction_run",
        "trend_analysis": "position_skill_trend_run",
    }
    for task_id in dict.fromkeys(payload.ids):
        try:
            record = container.tasks.get(actor, task_id, set(resource_types))
            items.append(trend_task_data(record, resource_types[record.task_type]))
        except TaskNotFound:
            missing.append(task_id)
        except PermissionDenied as exc:
            _raise(exc)
    return success_response(data=collection_data(
        items, page=1, page_size=max(len(items), 1), filters={"ids": payload.ids},
        sort_by="input_order", sort_order="asc", not_found_ids=missing,
    ))


@router.get(
    "/positions/{position_id}/trend-reports",
    response_model=TrendDeliveryCollectionEnvelope,
)
def get_position_trend_reports(
    position_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    min_source_coverage: float | None = Query(default=None, ge=0, le=1),
    quality_flag: str | None = None,
    sort_by: Literal["created_at", "updated_at", "source_coverage", "time_window_start"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageTrendReports = Depends(get_trend_report_use_cases),
):
    try:
        items = use_cases.list_by_position(position_id, actor)
    except (TrendPositionNotFound, PermissionDenied) as exc:
        _raise(exc)
    values = [
        trend_report_data(item, use_cases.delivery_status_for_record(item))
        for item in items
    ]
    if status:
        values = [item for item in values if item["status"] == status]
    if min_source_coverage is not None:
        values = [item for item in values if (item["source_coverage"] or 0) >= min_source_coverage]
    if quality_flag:
        values = [item for item in values if quality_flag in item["quality_flags"]]
    values.sort(
        key=lambda item: (item.get(sort_by) is None, item.get(sort_by)),
        reverse=sort_order == "desc",
    )
    return success_response(data=collection_data(
        values, page=page, page_size=page_size,
        filters={
            "position_id": position_id, "status": status,
            "min_source_coverage": min_source_coverage, "quality_flag": quality_flag,
        },
        sort_by=sort_by, sort_order=sort_order,
    ))


@router.post("/trend-reports/batch-query", response_model=TrendDeliveryCollectionEnvelope)
def batch_query_trend_reports(payload: TrendBatchQuery, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    items = []
    missing = []
    for report_id in dict.fromkeys(payload.ids):
        try:
            record = use_cases.get(report_id, actor)
            delivery = use_cases.delivery_status(actor, report_id)
            items.append(trend_report_data(record, delivery))
        except TrendReportNotFound:
            missing.append(report_id)
        except PermissionDenied as exc:
            _raise(exc)
    return success_response(data=collection_data(
        items, page=1, page_size=max(len(items), 1), filters={"ids": payload.ids},
        sort_by="input_order", sort_order="asc", not_found_ids=missing,
    ))


@router.get("/trend-reports/{report_id}", response_model=TrendDeliveryEnvelope)
def get_trend_report_detail(report_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    record = _get(report_id, use_cases, actor)
    return success_response(data=trend_report_data(record, use_cases.delivery_status(actor, report_id)))


@router.put("/trend-reports/{report_id}", response_model=TrendDeliveryEnvelope)
def edit_trend_report(report_id: str, payload: TrendReportUpdate, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    try:
        item = use_cases.update(actor, report_id, payload.reason.strip(), _changes(payload))
    except (PermissionDenied, TrendReportNotFound, TrendRuleViolation) as exc:
        _raise(exc)
    return success_response(data=trend_report_data(item, use_cases.delivery_status(actor, report_id)))


@router.post("/trend-reports/{report_id}/publish", response_model=TrendDeliveryEnvelope)
def publish_trend_report(report_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    try:
        item = use_cases.publish(actor, report_id)
    except (PermissionDenied, TrendReportNotFound, TrendRuleViolation) as exc:
        _raise(exc)
    return success_response(data=trend_report_data(item, use_cases.delivery_status(actor, report_id)))


@router.get("/trend-reports/{report_id}/export")
def export_trend_report(report_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    return success_response(data={"format": "json", "report": trend_report_data(_get(report_id, use_cases, actor))})


def _field(report_id: str, field: str, use_cases: ManageTrendReports, actor: AccountActor):
    item = _get(report_id, use_cases, actor)
    value = getattr(item, field)
    if field == "current_graph":
        return graph_data(value)
    if field == "skill_weight_distribution":
        return distribution_data(value)
    serializers = {
        "new_skills": skill_data,
        "rising_skills": skill_data,
        "declining_skills": skill_data,
        "replaced_skills": replacement_data,
        "skill_combo_shifts": combo_data,
        "risks": risk_data,
    }
    if field in serializers:
        return [serializers[field](entry) for entry in value]
    return value


@router.get("/trend-reports/{report_id}/current-graph")
def get_trend_report_current_graph(report_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    return success_response(data=_field(report_id, "current_graph", use_cases, actor))


@router.get("/trend-reports/{report_id}/skill-weight-distribution")
def get_trend_report_skill_weight_distribution(report_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    return success_response(data=_field(report_id, "skill_weight_distribution", use_cases, actor))


def _add_list_field_route(path: str, field: str):
    def endpoint(report_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
        return success_response(data=_field(report_id, field, use_cases, actor))
    endpoint.__name__ = f"get_trend_report_{field}"
    router.add_api_route(f"/trend-reports/{{report_id}}/{path}", endpoint, methods=["GET"])


for _path, _field_name in (
    ("new-skills", "new_skills"), ("rising-skills", "rising_skills"),
    ("declining-skills", "declining_skills"), ("replaced-skills", "replaced_skills"),
    ("skill-combo-shifts", "skill_combo_shifts"), ("risks", "risks"),
):
    _add_list_field_route(_path, _field_name)


@router.get("/trend-reports/{report_id}/summary")
def get_trend_report_summary(report_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageTrendReports = Depends(get_trend_report_use_cases)):
    item = _get(report_id, use_cases, actor)
    return success_response(data={"report_id": item.report_id, "summary": item.summary})


@router.post("/trend-change/analyses")
def create_trend_change_analysis(
    payload: CreateTrendChangeAnalysisRequest,
    actor: AccountActor = Depends(get_account_actor),
    container: ApplicationContainer = Depends(get_application_container),
):
    gateway = container.trend_intelligence_gateway
    result = gateway.create_trend_change_analysis(payload.model_dump(mode="json"))
    return success_response(data=result)


@router.post("/trend-change/analyses/from-history")
def create_trend_change_from_history(
    payload: CreateTrendChangeFromHistoryRequest,
    actor: AccountActor = Depends(get_account_actor),
    container: ApplicationContainer = Depends(get_application_container),
):
    gateway = container.trend_intelligence_gateway
    result = gateway.create_trend_change_from_history(payload.model_dump(mode="json"))
    return success_response(data=result)


@router.get("/trend-change/analyses/{analysis_id}")
def get_trend_change_analysis(
    analysis_id: str,
    subject_id: str | None = None,
    window: str | None = None,
    trend_state: str | None = None,
    actor: AccountActor = Depends(get_account_actor),
    container: ApplicationContainer = Depends(get_application_container),
):
    gateway = container.trend_intelligence_gateway
    result = gateway.get_trend_change_analysis(
        analysis_id, subject_id=subject_id, window=window, trend_state=trend_state
    )
    return success_response(data=result)


@router.get("/trend-change/analyses/{analysis_id}/change-points")
def get_trend_change_points(
    analysis_id: str,
    subject_id: str | None = None,
    window: str | None = None,
    trend_state: str | None = None,
    actor: AccountActor = Depends(get_account_actor),
    container: ApplicationContainer = Depends(get_application_container),
):
    gateway = container.trend_intelligence_gateway
    result = gateway.get_trend_change_points(
        analysis_id, subject_id=subject_id, window=window, trend_state=trend_state
    )
    return success_response(data=result)
