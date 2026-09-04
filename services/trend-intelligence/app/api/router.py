from __future__ import annotations

import hmac
import csv
import io
import json
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import text

from app.api.schemas import (
    ConfigurationTransitionRequest,
    CreateAnalysisRunRequest,
    CreateBacktestRequest,
    CreateConfigurationRequest,
    CreateEvaluationDatasetRequest,
    CreateTrendChangeAnalysisRequest,
    CreateTrendChangeFromHistoryRequest,
    DatasetActorRequest,
    GenerateEvaluationSamplesRequest,
    ReplayAnalysisRequest,
    ReviewEvaluationLabelRequest,
    ReviseEvaluationDatasetRequest,
    SubmitEvaluationLabelRequest,
)
from app.application.credibility import CredibilityService
from app.application.evaluation import EvaluationDatasetService
from app.application.service import AnalysisRunService
from app.application.trend_change import TrendChangeService
from app.ports.repository import IdempotencyConflict

router = APIRouter()


def service(request: Request) -> AnalysisRunService:
    return request.app.state.analysis_service


def credibility(request: Request) -> CredibilityService:
    return request.app.state.credibility_service


def evaluation(request: Request) -> EvaluationDatasetService:
    return request.app.state.evaluation_service


def trend_change(request: Request) -> TrendChangeService:
    return request.app.state.trend_change_service


def require_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = request.app.state.internal_token
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization.removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def envelope(data: object) -> dict[str, object]:
    return {"code": 0, "message": "success", "data": data}


async def _read_upload(file: UploadFile, maximum_size: int) -> bytes:
    raw = await file.read(maximum_size + 1)
    if len(raw) > maximum_size:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds configured size limit of {maximum_size} bytes",
        )
    return raw


@router.post(
    "/internal/v1/analysis-runs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_token)],
)
def create_run(payload: CreateAnalysisRunRequest, use_cases: AnalysisRunService = Depends(service)):
    try:
        run = use_cases.create(payload.to_command())
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    return envelope(run.to_dict())


@router.get(
    "/internal/v1/analysis-runs/{run_id}",
    dependencies=[Depends(require_token)],
)
def get_run(run_id: str, use_cases: AnalysisRunService = Depends(service)):
    run = use_cases.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return envelope(run.to_dict())


@router.get(
    "/internal/v1/analysis-runs/{run_id}/logs",
    dependencies=[Depends(require_token)],
)
def get_logs(run_id: str, use_cases: AnalysisRunService = Depends(service)):
    logs = use_cases.logs(run_id)
    if logs is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return envelope([asdict(item) for item in logs])


@router.get(
    "/internal/v1/analysis-runs/{run_id}/sources",
    dependencies=[Depends(require_token)],
)
def get_sources(run_id: str, use_cases: AnalysisRunService = Depends(service)):
    report = use_cases.source_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return envelope(report)


@router.get(
    "/internal/v1/analysis-runs/{run_id}/signals",
    dependencies=[Depends(require_token)],
)
def get_signals(run_id: str, use_cases: AnalysisRunService = Depends(service)):
    signals = use_cases.signals(run_id)
    if signals is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return envelope(signals)


@router.get(
    "/internal/v1/analysis-runs/{run_id}/predictions",
    dependencies=[Depends(require_token)],
)
def get_predictions(run_id: str, use_cases: AnalysisRunService = Depends(service)):
    predictions = use_cases.predictions(run_id)
    if predictions is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return envelope(predictions)


@router.get(
    "/internal/v1/analysis-runs/{run_id}/skill-trends",
    dependencies=[Depends(require_token)],
)
def get_skill_trends(run_id: str, use_cases: AnalysisRunService = Depends(service)):
    result = use_cases.position_skill_trend(run_id)
    if result is None:
        run = use_cases.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="analysis run not found")
        raise HTTPException(status_code=409, detail="position skill trend result is not ready")
    return envelope(result)


@router.post(
    "/internal/v1/trend-change/analyses",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_token)],
)
def create_trend_change_analysis(
    payload: CreateTrendChangeAnalysisRequest,
    use_cases: TrendChangeService = Depends(trend_change),
):
    return envelope(use_cases.analyze(payload.model_dump()))


@router.post(
    "/internal/v1/trend-change/analyses/from-history",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_token)],
)
def create_trend_change_from_history(
    payload: CreateTrendChangeFromHistoryRequest,
    use_cases: TrendChangeService = Depends(trend_change),
):
    try:
        result = use_cases.analyze_from_history(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope(result)


@router.get(
    "/internal/v1/trend-change/analyses/{analysis_id}",
    dependencies=[Depends(require_token)],
)
def get_trend_change_analysis(
    analysis_id: str,
    use_cases: TrendChangeService = Depends(trend_change),
    subject_id: str | None = None,
    window: str | None = None,
    trend_state: Literal["rising", "accelerating", "stable", "declining", "volatile"] | None = None,
):
    result = use_cases.get(
        analysis_id,
        subject_id=subject_id,
        window=window,
        trend_state=trend_state,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="trend change analysis not found")
    return envelope(result)


@router.get(
    "/internal/v1/trend-change/analyses/{analysis_id}/change-points",
    dependencies=[Depends(require_token)],
)
def get_trend_change_points(
    analysis_id: str,
    use_cases: TrendChangeService = Depends(trend_change),
    subject_id: str | None = None,
    window: str | None = None,
    trend_state: Literal["rising", "accelerating", "stable", "declining", "volatile"] | None = None,
):
    result = use_cases.change_points(
        analysis_id,
        subject_id=subject_id,
        window=window,
        trend_state=trend_state,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="trend change analysis not found")
    return envelope(result)


@router.post(
    "/internal/v1/analysis-runs/{run_id}/cancel",
    dependencies=[Depends(require_token)],
)
def cancel_run(run_id: str, use_cases: AnalysisRunService = Depends(service)):
    run = use_cases.cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return envelope(run.to_dict())


@router.post("/internal/v1/analysis-runs/{run_id}/replay", status_code=202, dependencies=[Depends(require_token)])
def replay_run(run_id: str, payload: ReplayAnalysisRequest, use_cases: AnalysisRunService = Depends(service)):
    try:
        run = use_cases.replay(run_id, payload.request_id, payload.idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return envelope(run.to_dict())


@router.post("/internal/v1/evaluation-datasets", dependencies=[Depends(require_token)])
def create_evaluation_dataset(payload: CreateEvaluationDatasetRequest,
                              use_cases: EvaluationDatasetService = Depends(evaluation)):
    try:
        return envelope(use_cases.create(payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/internal/v1/evaluation-datasets/{dataset_id}", dependencies=[Depends(require_token)])
def get_evaluation_dataset(dataset_id: str, use_cases: EvaluationDatasetService = Depends(evaluation)):
    result = use_cases.store.get_dataset(dataset_id)
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation dataset not found")
    return envelope(result)


@router.post("/internal/v1/evaluation-datasets/{dataset_id}/samples/generate", dependencies=[Depends(require_token)])
def generate_evaluation_samples(dataset_id: str, payload: GenerateEvaluationSamplesRequest,
                                use_cases: EvaluationDatasetService = Depends(evaluation)):
    try:
        records = [item.model_dump() for item in payload.records]
        return envelope(use_cases.generate_samples(dataset_id, payload.source_type, records, payload.actor))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/internal/v1/evaluation-datasets/{dataset_id}/samples/import", dependencies=[Depends(require_token)])
async def import_evaluation_samples(dataset_id: str, actor: Annotated[str, Form()],
                                    file: Annotated[UploadFile, File()],
                                    request: Request,
                                    use_cases: EvaluationDatasetService = Depends(evaluation)):
    raw = (await _read_upload(file, request.app.state.max_upload_size_bytes)).decode("utf-8-sig")
    try:
        if file.filename and file.filename.lower().endswith(".csv"):
            records = list(csv.DictReader(io.StringIO(raw)))
            for item in records:
                item["evidence"] = json.loads(item.get("evidence") or "[]")
        else:
            value = json.loads(raw)
            records = value if isinstance(value, list) else value["records"]
        validated = GenerateEvaluationSamplesRequest(
            source_type="manual_import", actor=actor, records=records,
        )
        return envelope(use_cases.generate_samples(
            dataset_id, "manual_import", [item.model_dump() for item in validated.records], actor,
        ))
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid import file: {exc}") from exc


@router.get("/internal/v1/evaluation-datasets/{dataset_id}/samples", dependencies=[Depends(require_token)])
def list_evaluation_samples(dataset_id: str, sample_status: str | None = None,
                            use_cases: EvaluationDatasetService = Depends(evaluation)):
    result = use_cases.store.list_samples(dataset_id, sample_status)
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation dataset not found")
    return envelope(result)


@router.post("/internal/v1/evaluation-samples/{sample_id}/labels", dependencies=[Depends(require_token)])
def submit_evaluation_label(sample_id: str, payload: SubmitEvaluationLabelRequest,
                            use_cases: EvaluationDatasetService = Depends(evaluation)):
    try:
        result = use_cases.submit_label(sample_id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation sample not found")
    return envelope(result)


@router.post("/internal/v1/evaluation-labels/{label_id}/review", dependencies=[Depends(require_token)])
def review_evaluation_label(label_id: str, payload: ReviewEvaluationLabelRequest,
                            use_cases: EvaluationDatasetService = Depends(evaluation)):
    try:
        result = use_cases.review_label(label_id, payload.decision, payload.reviewer_id, payload.review_note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation label not found")
    return envelope(result)


@router.post("/internal/v1/evaluation-datasets/{dataset_id}/publish", dependencies=[Depends(require_token)])
def publish_evaluation_dataset(dataset_id: str, payload: DatasetActorRequest,
                               use_cases: EvaluationDatasetService = Depends(evaluation)):
    try:
        result = use_cases.store.publish_dataset(dataset_id, payload.actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation dataset not found")
    return envelope(result)


@router.post("/internal/v1/evaluation-datasets/{dataset_id}/revisions", dependencies=[Depends(require_token)])
def revise_evaluation_dataset(dataset_id: str, payload: ReviseEvaluationDatasetRequest,
                              use_cases: EvaluationDatasetService = Depends(evaluation)):
    try:
        result = use_cases.revise(dataset_id, payload.version, payload.actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation dataset not found")
    return envelope(result)


@router.get("/internal/v1/evaluation-datasets/{dataset_id}/history", dependencies=[Depends(require_token)])
def evaluation_dataset_history(dataset_id: str, use_cases: EvaluationDatasetService = Depends(evaluation)):
    result = use_cases.store.history(dataset_id)
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation dataset not found")
    return envelope(result)


@router.get("/internal/v1/source-health", dependencies=[Depends(require_token)])
def source_health(request: Request, source: str | None = None):
    return envelope(request.app.state.source_governance.source_health(source))


@router.post("/internal/v1/configurations", dependencies=[Depends(require_token)])
def create_configuration(payload: CreateConfigurationRequest, use_cases: CredibilityService = Depends(credibility)):
    try:
        return envelope(use_cases.create_configuration(payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/internal/v1/configurations", dependencies=[Depends(require_token)])
def list_configurations(config_type: str | None = None, use_cases: CredibilityService = Depends(credibility)):
    return envelope(use_cases.store.list_configurations(config_type))


@router.get("/internal/v1/configurations/compare", dependencies=[Depends(require_token)])
def compare_configurations(left_id: str, right_id: str, use_cases: CredibilityService = Depends(credibility)):
    result = use_cases.store.compare(left_id, right_id)
    if result is None:
        raise HTTPException(status_code=404, detail="configuration not found")
    return envelope(result)


@router.get("/internal/v1/configurations/{config_id}", dependencies=[Depends(require_token)])
def get_configuration(config_id: str, use_cases: CredibilityService = Depends(credibility)):
    result = use_cases.store.get_configuration(config_id)
    if result is None:
        raise HTTPException(status_code=404, detail="configuration not found")
    return envelope(result)


@router.get("/internal/v1/configurations/{config_id}/history", dependencies=[Depends(require_token)])
def configuration_history(config_id: str, use_cases: CredibilityService = Depends(credibility)):
    result = use_cases.store.history(config_id)
    if result is None:
        raise HTTPException(status_code=404, detail="configuration not found")
    return envelope(result)


def _configuration_transition(config_id: str, action: str, payload: ConfigurationTransitionRequest, use_cases: CredibilityService):
    try:
        result = use_cases.store.transition(config_id, action, payload.actor, payload.review_note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="configuration not found")
    return envelope(result)


@router.post("/internal/v1/configurations/{config_id}/enable", dependencies=[Depends(require_token)])
def enable_configuration(config_id: str, payload: ConfigurationTransitionRequest, use_cases: CredibilityService = Depends(credibility)):
    return _configuration_transition(config_id, "enable", payload, use_cases)


@router.post("/internal/v1/configurations/{config_id}/disable", dependencies=[Depends(require_token)])
def disable_configuration(config_id: str, payload: ConfigurationTransitionRequest, use_cases: CredibilityService = Depends(credibility)):
    return _configuration_transition(config_id, "disable", payload, use_cases)


@router.post("/internal/v1/backtests", status_code=202, dependencies=[Depends(require_token)])
def create_backtest(payload: CreateBacktestRequest, use_cases: CredibilityService = Depends(credibility)):
    try:
        return envelope(use_cases.create_backtest(payload.to_payload()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/internal/v1/backtests/{backtest_id}", dependencies=[Depends(require_token)])
def get_backtest(backtest_id: str, use_cases: CredibilityService = Depends(credibility)):
    result = use_cases.store.get_backtest(backtest_id)
    if result is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return envelope(result)


@router.get("/internal/v1/backtests/{backtest_id}/metrics", dependencies=[Depends(require_token)])
def get_backtest_metrics(backtest_id: str, use_cases: CredibilityService = Depends(credibility)):
    result = use_cases.backtest_metrics(backtest_id)
    if result is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return envelope(result)


@router.get(
    "/internal/v1/analysis-runs/{run_id}/predictions/{prediction_id}/explanation",
    dependencies=[Depends(require_token)],
)
def get_prediction_explanation(run_id: str, prediction_id: str, use_cases: AnalysisRunService = Depends(service)):
    result = use_cases.prediction_explanation(run_id, prediction_id)
    if result is None:
        raise HTTPException(status_code=404, detail="prediction not found")
    return envelope(result)


@router.post("/internal/v1/acquisition/bundles/{bundle_id}/import", dependencies=[Depends(require_token)])
def import_bundle(bundle_id: str, request: Request):
    try:
        result = request.app.state.trend_input_adapter.import_bundle(bundle_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(result.to_dict())


@router.get("/health/live")
def liveness():
    return {"status": "ok"}


@router.get("/readiness")
def readiness(request: Request, response: Response):
    try:
        with request.app.state.database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        response.status_code = 503
        return {"status": "not_ready"}
    return {"status": "ready"}
