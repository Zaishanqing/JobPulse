from __future__ import annotations
from app.domain.json_types import FrozenJsonObject as _FrozenJsonObject

from dataclasses import dataclass as _dataclass
from datetime import date as _date

from app.contexts.discovery.application_types import ClusterJDRecord, ClusterProjection
from app.contexts.discovery.domain import Actor, ReleasedJDFact
from app.domain.values import FrozenDict as _FrozenDict, thaw as _thaw


@_dataclass(frozen=True)
class HistoricalTimeWindow:
    window_id: str
    start: _date
    end: _date


@_dataclass(frozen=True)
class DiscoveryRunRequest:
    contract_version: str
    request_id: str
    algorithm: str
    time_window_start: _date | None
    time_window_end: _date | None
    snapshots: tuple[ReleasedJDFact, ...]
    config: _FrozenDict[str, object]
    time_windows: tuple[HistoricalTimeWindow, ...]
    current_observation_window_id: str


@_dataclass(frozen=True)
class DiscoveryClusterResult:
    cluster_id: str
    cluster_name: str
    sample_count: int
    core_skills: tuple[_FrozenDict[str, object], ...]
    representative_titles: tuple[str, ...]
    representative_jd_ids: tuple[str, ...]
    stability_score: float
    growth_score: float
    distance_from_existing_positions: float
    emergence_assessment: _FrozenDict[str, object]
    generated_definition: _FrozenDict[str, object]
    standard_position_comparison: _FrozenDict[str, object] = _FrozenDict()
    explainability: _FrozenDict[str, object] = _FrozenDict()
    lineage_relations: tuple[_FrozenDict[str, object], ...] = ()


@_dataclass(frozen=True)
class DiscoveryRunResult:
    run_id: str
    status: str
    run_id: str
    algorithm_version: str
    clusters: tuple[DiscoveryClusterResult, ...]
    lineages: tuple[_FrozenDict[str, object], ...] = ()
    request_id: str = ""
    input_fingerprint: str | None = None
    input_quality_report: _FrozenDict[str, object] = _FrozenDict()
    run_context: _FrozenDict[str, object] = _FrozenDict()
    provider: str = "emerging_discovery_http"
    implementation_status: str = "remote_vector_discovery_service"
    mock: bool = False
    rule_based: bool = False


def _skill_reference(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        raw = value.get("name") or value.get("raw_skill") or value.get("canonical_name")
        skill_id = value.get("skill_id") or value.get("normalized_skill_id")
        result: dict[str, object] = {}
        if raw is not None:
            result["raw_skill"] = str(raw)
        if skill_id is not None:
            result["normalized_skill_id"] = str(skill_id)
        return result
    return {"raw_skill": str(value)}


def released_jd_contract(fact: ReleasedJDFact) -> _FrozenJsonObject:
    """Canonical discovery.v2 representation shared by identity and HTTP mapping."""
    structured_data = dict(_thaw(fact.structured_data))
    for key in ("required_skills", "bonus_skills"):
        items = structured_data.get(key)
        if isinstance(items, list):
            structured_data[key] = [_skill_reference(item) for item in items]
    for key, value in (
        ("source_record_id", fact.source_record_id),
        ("bundle_id", fact.bundle_id),
        ("date_source", fact.date_source),
    ):
        if value is not None:
            structured_data[key] = value
    result = {
        "source_fact_id": fact.source_fact_id,
        "source_fact_version": fact.source_fact_version,
        "jd_id": fact.jd_id,
        "schema_version": fact.schema_version,
        "review_status": fact.review_status,
        "consumption_path": fact.consumption_path,
        "title": fact.title,
        "source_name": fact.source_name,
        "publish_date": fact.publish_date.isoformat() if fact.publish_date else None,
        "structured_data": structured_data,
    }
    if fact.content_hash is not None:
        result["content_hash"] = fact.content_hash
    return result


__all__ = [
    "Actor",
    "ClusterJDRecord",
    "ClusterProjection",
    "DiscoveryClusterResult",
    "DiscoveryRunRequest",
    "DiscoveryRunResult",
    "HistoricalTimeWindow",
    "ReleasedJDFact",
    "released_jd_contract",
]

del annotations
