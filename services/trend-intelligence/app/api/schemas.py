from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.analysis_run import NewAnalysisRun


class TimeWindow(BaseModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_window(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("time window timestamps must include a timezone")
        if self.start >= self.end:
            raise ValueError("time window start must be before end")
        return self


class CreateAnalysisRunRequest(BaseModel):
    contract_version: Literal["trend-analysis.v2"] = "trend-analysis.v2"
    request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    time_window: TimeWindow
    data_sources: list[str] = Field(min_length=1, max_length=32)
    weights: dict[str, float] = Field(min_length=1, max_length=64)
    algorithm_version: str = Field(min_length=1, max_length=128)
    formula_version: str = Field(min_length=1, max_length=128)
    run_type: Literal["market_prediction", "position_skill_trend"] = "market_prediction"
    position_id: str | None = Field(default=None, min_length=1, max_length=128)
    position_name: str | None = Field(default=None, min_length=1, max_length=255)
    graph_version: str | None = Field(default=None, min_length=1, max_length=128)
    standard_skills: list[dict[str, Any]] | None = None
    skill_catalog_version: str | None = Field(default=None, min_length=1, max_length=128)
    config_version: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_run_type(self):
        if self.run_type == "position_skill_trend":
            required = (
                self.position_id, self.position_name, self.graph_version,
                self.standard_skills, self.skill_catalog_version, self.config_version,
            )
            if any(value is None or value == [] for value in required):
                raise ValueError("position_skill_trend requires position, graph, skills, catalog and config versions")
            for skill in self.standard_skills or []:
                if not skill.get("skill_id") or not skill.get("skill_name"):
                    raise ValueError("standard skills require skill_id and skill_name")
                if not isinstance(skill.get("aliases", []), list):
                    raise ValueError("skill aliases must be a list")
            self.standard_skills = sorted(
                (
                    {
                        "skill_id": str(skill["skill_id"]).strip(),
                        "skill_name": str(skill["skill_name"]).strip(),
                        "aliases": sorted({
                            str(alias).strip() for alias in skill.get("aliases", [])
                            if str(alias).strip()
                        }),
                    }
                    for skill in self.standard_skills or []
                ),
                key=lambda skill: skill["skill_id"],
            )
        return self

    @field_validator("request_id", "idempotency_key", "algorithm_version", "formula_version")
    @classmethod
    def reject_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("data_sources")
    @classmethod
    def normalize_sources(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("data source must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("data sources must be unique")
        return normalized

    @field_validator("weights")
    @classmethod
    def validate_weights(cls, values: dict[str, float]) -> dict[str, float]:
        if any(not key.strip() for key in values):
            raise ValueError("weight keys must not be blank")
        if any(value < 0 or value > 1 for value in values.values()):
            raise ValueError("weights must be between 0 and 1")
        if sum(values.values()) <= 0:
            raise ValueError("at least one weight must be positive")
        return values

    def to_command(self) -> NewAnalysisRun:
        run_payload = {
            key: value
            for key, value in {
                "position_id": self.position_id,
                "position_name": self.position_name,
                "graph_version": self.graph_version,
                "standard_skills": self.standard_skills,
                "skill_catalog_version": self.skill_catalog_version,
                "config_version": self.config_version,
            }.items()
            if value is not None
        }
        return NewAnalysisRun(
            contract_version=self.contract_version,
            request_id=self.request_id.strip(),
            idempotency_key=self.idempotency_key.strip() if self.idempotency_key else None,
            window_start=self.time_window.start,
            window_end=self.time_window.end,
            data_sources=tuple(self.data_sources),
            weights=self.weights,
            algorithm_version=self.algorithm_version.strip(),
            formula_version=self.formula_version.strip(),
            run_type=self.run_type,
            run_payload=run_payload,
        )


class CreateConfigurationRequest(BaseModel):
    config_type: Literal[
        "job_knowledge", "policy_keywords", "domain_dictionary",
        "github_topics", "trend_thresholds",
    ]
    version: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(min_length=1)
    created_by: str = Field(min_length=1, max_length=128)


class ConfigurationTransitionRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=128)
    review_note: str | None = Field(default=None, max_length=2000)


class BacktestSliceRequest(BaseModel):
    slice_key: str = Field(min_length=1, max_length=128)
    observation_cutoff: datetime
    validation_end: datetime
    weights: dict[str, float] | None = None
    weight_variants: list[dict[str, float]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.observation_cutoff.tzinfo is None or self.validation_end.tzinfo is None:
            raise ValueError("backtest timestamps must include timezone")
        if self.validation_end <= self.observation_cutoff:
            raise ValueError("validation end must be after observation cutoff")
        return self


class CreateBacktestRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=36)
    dataset_version: str = Field(min_length=1, max_length=128)
    config_versions: dict[str, str] | None = None
    k: int = Field(default=10, ge=1, le=100)
    time_slices: list[BacktestSliceRequest] = Field(min_length=1, max_length=100)

    def to_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class CreateEvaluationDatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    created_by: str = Field(min_length=1, max_length=128)


class EvaluationSampleInput(BaseModel):
    entity_type: Literal["position", "skill"]
    entity_id: str = Field(min_length=1, max_length=128)
    entity_name: str = Field(min_length=1, max_length=255)
    prediction_cutoff: datetime
    label_window_start: datetime
    label_window_end: datetime
    source_reference: str = Field(min_length=1, max_length=1000)
    source_dedup_key: str | None = Field(default=None, max_length=128)
    evidence: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_window(self):
        values = (self.prediction_cutoff, self.label_window_start, self.label_window_end)
        if any(value.tzinfo is None for value in values):
            raise ValueError("evaluation timestamps must include timezone")
        if self.label_window_start < self.prediction_cutoff:
            raise ValueError("label window cannot start before prediction cutoff")
        if self.label_window_end <= self.label_window_start:
            raise ValueError("label window end must be after start")
        return self


class GenerateEvaluationSamplesRequest(BaseModel):
    source_type: Literal["published_position_graph", "historical_hiring", "manual_import"]
    actor: str = Field(min_length=1, max_length=128)
    records: list[EvaluationSampleInput] = Field(min_length=1, max_length=10000)


class SubmitEvaluationLabelRequest(BaseModel):
    label_type: Literal["position_change", "skill_change", "hiring_change"]
    direction: Literal["new", "rising", "declining", "stable"]
    observed_value: float | None = None
    evidence: list[dict[str, Any]] = Field(min_length=1)
    confidence_level: Literal["low", "medium", "high"]
    annotator_id: str = Field(min_length=1, max_length=128)


class ReviewEvaluationLabelRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer_id: str = Field(min_length=1, max_length=128)
    review_note: str | None = Field(default=None, max_length=2000)


class DatasetActorRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=128)


class ReviseEvaluationDatasetRequest(DatasetActorRequest):
    version: str = Field(min_length=1, max_length=128)


class ReplayAnalysisRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class TrendWindowScoreRequest(BaseModel):
    window: str = Field(min_length=1, max_length=128)
    score: float = Field(ge=0, le=1)
    duration_days: float = Field(default=1.0, gt=0)
    source_diversity: int = Field(default=0, ge=0, le=1000)
    source_records: list[str] = Field(default_factory=list, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=1000)
    trend_report_id: str | None = Field(default=None, max_length=128)
    analysis_run_id: str | None = Field(default=None, max_length=128)
    source_count: int = Field(default=0, ge=0, le=100000)
    algorithm_version: str | None = Field(default=None, max_length=128)
    window_start: str | None = Field(default=None, max_length=64)
    window_end: str | None = Field(default=None, max_length=64)


class TrendChangeSubjectRequest(BaseModel):
    subject_id: str = Field(min_length=1, max_length=128)
    subject_type: str = Field(min_length=1, max_length=64)
    windows: list[TrendWindowScoreRequest] = Field(min_length=2, max_length=200)

    @model_validator(mode="after")
    def validate_windows(self):
        labels = [item.window for item in self.windows]
        if len(set(labels)) != len(labels):
            raise ValueError("window labels must be unique within a subject")
        return self


class CreateTrendChangeAnalysisRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    subjects: list[TrendChangeSubjectRequest] = Field(min_length=1, max_length=100)


class CreateTrendChangeFromHistoryRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    subject_id: str = Field(min_length=1, max_length=128)
    subject_type: Literal["job", "skill"]
    from_time: datetime | None = None
    to_time: datetime | None = None
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_times(self):
        for value in (self.from_time, self.to_time):
            if value is not None and value.tzinfo is None:
                raise ValueError("from_time and to_time must include timezone")
        if (
            self.from_time is not None
            and self.to_time is not None
            and self.from_time > self.to_time
        ):
            raise ValueError("from_time must not be after to_time")
        return self
