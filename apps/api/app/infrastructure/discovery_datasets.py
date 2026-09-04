"""Read-only adapters for preregistered discovery experiment datasets."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.contexts.discovery import (
    ClusterJDRecord,
    FROZEN_DISCOVERY_DATASET_ID,
    HistoricalTimeWindow,
    ReleasedJDFact,
)


_SAMPLE_SHA256 = "d174ab44d7f79ca3b5b13a9cf980eb5bd522648fd2862a0b099aca995b4ec3ff"
_SAMPLE_COUNT = 127
_WINDOW_SHA256 = "44ab36e8b1ca8c62e273eb54fb9779a87be1c4f657297fcbcf45f46036d29e9c"
_SAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "emerging-discovery"
    / f"{FROZEN_DISCOVERY_DATASET_ID}.sample.jsonl"
)
_WINDOW_PATH = _SAMPLE_PATH.with_name(f"{FROZEN_DISCOVERY_DATASET_ID}.windows.json")
_FORMAL_EXPERIMENT_PATH = _SAMPLE_PATH.with_name(
    "exp-emerge-01-crosswindow-v3.2-20260823.summary.json"
)
_FORMAL_CLUSTERS_PATH = _SAMPLE_PATH.with_name(
    "exp-emerge-01-crosswindow-v3.2-20260823.clusters.json"
)
_FORMAL_SCENARIOS_PATH = _SAMPLE_PATH.with_name(
    "exp-emerge-01-crosswindow-v3.2-20260823.scenarios.json"
)


def _required_text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Frozen discovery sample has invalid {field}")
    return value.strip()


@lru_cache(maxsize=1)
def frozen_discovery_rows() -> tuple[dict[str, Any], ...]:
    payload = _SAMPLE_PATH.read_bytes()
    if hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest() != _SAMPLE_SHA256:
        raise ValueError("Frozen discovery sample manifest digest mismatch")
    rows = tuple(
        json.loads(line)
        for line in payload.decode("utf-8").splitlines()
        if line.strip()
    )
    if len(rows) != _SAMPLE_COUNT:
        raise ValueError("Frozen discovery sample manifest count mismatch")
    sample_ids = {_required_text(row, "sample_id") for row in rows}
    if len(sample_ids) != len(rows):
        raise ValueError("Frozen discovery sample IDs are not unique")
    for row in rows:
        if row.get("dataset_version") != FROZEN_DISCOVERY_DATASET_ID:
            raise ValueError("Frozen discovery dataset version mismatch")
        date.fromisoformat(_required_text(row, "publish_date"))
        _required_text(row, "source_platform")
        _required_text(row, "source_record_id")
        _required_text(row, "content_hash")
        if not isinstance(row.get("responsibilities"), list):
            raise ValueError("Frozen discovery responsibilities must be a list")
        if not isinstance(row.get("skills"), list):
            raise ValueError("Frozen discovery skills must be a list")
    return rows


def _jd_id(row: dict[str, Any]) -> str:
    return f"frozen-d5:{_required_text(row, 'sample_id')}"


@lru_cache(maxsize=1)
def frozen_discovery_windows() -> tuple[HistoricalTimeWindow, ...]:
    payload = _WINDOW_PATH.read_bytes()
    if hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest() != _WINDOW_SHA256:
        raise ValueError("Frozen discovery window manifest digest mismatch")
    manifest = json.loads(payload)
    if manifest.get("dataset_version") != FROZEN_DISCOVERY_DATASET_ID:
        raise ValueError("Frozen discovery window dataset version mismatch")
    windows = tuple(
        HistoricalTimeWindow(
            window_id=_required_text(item, "window_id"),
            start=date.fromisoformat(_required_text(item, "start")),
            end=date.fromisoformat(_required_text(item, "end")),
        )
        for item in manifest.get("windows", [])
    )
    if len(windows) != 4 or any(window.start > window.end for window in windows):
        raise ValueError("Frozen discovery windows are invalid")
    expected_counts = {
        str(item["window_id"]): int(item["sample_count"])
        for item in manifest["windows"]
    }
    actual_counts = {
        window.window_id: sum(
            row.get("window_id") == window.window_id for row in frozen_discovery_rows()
        )
        for window in windows
    }
    if actual_counts != expected_counts:
        raise ValueError("Frozen discovery window counts do not match samples")
    return windows


def list_frozen_discovery_facts(dataset_id: str) -> list[ReleasedJDFact]:
    if dataset_id != FROZEN_DISCOVERY_DATASET_ID:
        raise LookupError(dataset_id)
    facts = []
    for row in frozen_discovery_rows():
        platform = _required_text(row, "source_platform")
        responsibilities = tuple(str(item) for item in row["responsibilities"])
        skills = tuple(str(item) for item in row["skills"])
        facts.append(
            ReleasedJDFact(
                source_fact_id=_required_text(row, "source_fact_id"),
                source_fact_version=_required_text(row, "source_fact_version"),
                jd_id=_jd_id(row),
                title=_required_text(row, "title"),
                source_name=platform,
                publish_date=date.fromisoformat(_required_text(row, "publish_date")),
                structured_data={
                    "position_title": _required_text(row, "title"),
                    "responsibilities": responsibilities,
                    "required_skills": tuple({"raw_skill": item} for item in skills),
                    "bonus_skills": (),
                    "industry": None,
                    "business_scenarios": (),
                    "company_name": row.get("company_name"),
                    "source_platform": platform,
                },
                content_hash=_required_text(row, "content_hash"),
                source_record_id=_required_text(row, "source_record_id"),
                bundle_id=FROZEN_DISCOVERY_DATASET_ID,
                date_source="publish_date",
                review_status="approved",
                consumption_path=None,
            )
        )
    return facts


def frozen_cluster_jds(jd_ids: list[str]) -> list[ClusterJDRecord]:
    by_id = {_jd_id(row): row for row in frozen_discovery_rows()}
    records = []
    for jd_id in jd_ids:
        row = by_id.get(jd_id)
        if row is None:
            continue
        responsibilities = [str(item) for item in row["responsibilities"]]
        records.append(
            ClusterJDRecord(
                jd_id=jd_id,
                source_type="frozen_experiment",
                source_name=_required_text(row, "source_platform"),
                enterprise_id=None,
                title=_required_text(row, "title"),
                raw_text="\n".join(responsibilities),
                publish_date=date.fromisoformat(_required_text(row, "publish_date")),
                url=(str(row["source_url"]) if row.get("source_url") else None),
                file_id=None,
                parse_status="completed",
                input_extraction_status="completed",
                input_provider="frozen_experiment_projection",
                input_error_code=None,
                input_error_message=None,
                copy_risk_score=None,
                inflation_score=None,
                is_downweighted=False,
                created_at=None,
                updated_at=None,
            )
        )
    return records


@lru_cache(maxsize=1)
def formal_discovery_experiment_report() -> dict[str, Any]:
    report = json.loads(_FORMAL_EXPERIMENT_PATH.read_text(encoding="utf-8"))
    if report.get("experiment_id") != "EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823":
        raise ValueError("Formal discovery experiment identity mismatch")
    counts = report.get("cluster_counts") or {}
    distribution = report.get("stage2_distribution_over_eligible") or {}
    if int(counts.get("clusters_eligible_for_stage2", -1)) != sum(
        int(value) for value in distribution.values()
    ):
        raise ValueError("Formal discovery experiment distribution is inconsistent")
    emerging = report.get("emerging_clusters") or []
    if len(emerging) != int(distribution.get("emerging", -1)):
        raise ValueError("Formal discovery emerging-cluster count is inconsistent")
    return report


@lru_cache(maxsize=1)
def formal_discovery_experiment_clusters() -> tuple[dict[str, Any], ...]:
    payload = json.loads(_FORMAL_CLUSTERS_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "emerge-v3.2-formal-cluster-projection.v1":
        raise ValueError("Formal discovery cluster projection schema mismatch")
    if payload.get("experiment_id") != "EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823":
        raise ValueError("Formal discovery cluster projection identity mismatch")
    clusters = deepcopy(payload.get("clusters") or [])
    if len(clusters) != 2811:
        raise ValueError("Formal discovery cluster projection count mismatch")
    eligible = sum(1 for item in clusters if bool(item.get("eligible")))
    if eligible != 2021:
        raise ValueError("Formal discovery cluster projection eligibility mismatch")
    scenario_payload = json.loads(_FORMAL_SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenario_definitions = scenario_payload.get("definitions") or {}
    emerging_clusters = [item for item in clusters if item.get("state") == "emerging"]
    if set(scenario_definitions) != {
        str(item.get("canonical_title")) for item in emerging_clusters
    }:
        raise ValueError("Formal discovery scenario definitions are incomplete")
    for cluster in emerging_clusters:
        title = str(cluster["canonical_title"])
        definition = cluster.get("definition")
        if not isinstance(definition, dict):
            raise ValueError(f"Formal discovery definition is unavailable: {title}")
        field_evidence = definition.get("field_evidence")
        if not isinstance(field_evidence, dict):
            raise ValueError(f"Formal discovery field evidence is unavailable: {title}")
        responsibility_field = field_evidence.get("core_responsibilities") or {}
        responsibility_items = responsibility_field.get("items") or []
        scenario_items: list[dict[str, Any]] = []
        for scenario in scenario_definitions[title]:
            label = str(scenario.get("scenario") or "").strip()
            fragment = str(scenario.get("evidence_fragment") or "").strip()
            matched = next(
                (
                    item
                    for item in responsibility_items
                    if fragment and fragment in str(item.get("content") or "")
                ),
                None,
            )
            if not label or not matched or not matched.get("evidence"):
                raise ValueError(
                    f"Formal discovery scenario lacks responsibility evidence: {title} / {label}"
                )
            scenario_items.append(
                {"content": label, "evidence": deepcopy(matched["evidence"])}
            )
        if not scenario_items:
            raise ValueError(f"Formal discovery scenarios are empty: {title}")
        definition["industry_scenarios"] = [item["content"] for item in scenario_items]
        field_evidence["industry_scenarios"] = {
            "content": list(definition["industry_scenarios"]),
            "items": scenario_items,
        }
        responsibilities = [
            str(value).strip().rstrip("；;。")
            for value in (definition.get("core_responsibilities") or [])
            if str(value).strip()
        ]
        skills = [
            str(value.get("raw_skill") or value.get("normalized_skill_id") or "").strip()
            for value in (definition.get("required_skills") or [])
            if isinstance(value, dict)
        ]
        scenarios = list(definition["industry_scenarios"])
        if not responsibilities or not skills or not scenarios:
            raise ValueError(f"Formal discovery summary inputs are incomplete: {title}")
        responsibility_clauses = [
            re.sub(r"^(?:负责|承担)", "", value).strip()
            for value in responsibilities[:2]
        ]
        responsibility_text = "；同时负责".join(responsibility_clauses)
        summary = (
            f"{title}聚焦于{'、'.join(scenarios[:3])}等业务场景，"
            f"主要负责{responsibility_text}。"
            f"该岗位通常需要掌握{'、'.join(skills[:6])}等核心技能。"
        )
        definition["position_summary"] = summary
        summary_field = field_evidence.get("position_summary")
        summary_data = dict(summary_field) if isinstance(summary_field, dict) else {}
        summary_data["content"] = summary
        field_evidence["position_summary"] = summary_data
    return tuple(clusters)


__all__ = [
    "FROZEN_DISCOVERY_DATASET_ID",
    "formal_discovery_experiment_clusters",
    "frozen_cluster_jds",
    "frozen_discovery_rows",
    "frozen_discovery_windows",
    "formal_discovery_experiment_report",
    "list_frozen_discovery_facts",
]
