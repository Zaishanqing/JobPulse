from collections.abc import Mapping

from app.domain.trend_analysis import (
    SkillComboShift,
    SkillReplacement,
    SkillWeightDistribution,
    TrendGraphSnapshot,
    TrendRelation,
    TrendRisk,
    TrendSkill,
)
from app.contexts.market_intelligence import TrendReportRecord
from app.api.trend_delivery_mapping import delivery_fields
from app.domain.json_types import freeze_json_object, thaw_json_object


def skill_data(skill: TrendSkill) -> dict[str, object]:
    data: dict[str, object] = {
        "skill_id": skill.skill_id,
        "skill_name": skill.skill_name,
        "category": skill.category,
        "weight": skill.weight,
        "confidence": skill.confidence,
        "importance_level": skill.importance_level,
        "trend_score": skill.trend_score,
        "evidence_count": skill.evidence_count,
        "growth_rate": skill.growth_rate,
        "trend_direction": skill.trend_direction,
        "evidence_references": list(skill.evidence_references),
        "quality_flags": list(skill.quality_flags),
        "score_explanation": thaw_json_object(skill.score_explanation)
        if skill.score_explanation else None,
        "current_window_signal": skill.current_window_signal,
        "historical_window_signal": skill.historical_window_signal,
    }
    if skill.created_at:
        data["created_at"] = skill.created_at
    return data


def skill_from_data(raw: Mapping[str, object]) -> TrendSkill:
    skill_id = str(raw.get("skill_id") or raw.get("normalized_skill_id") or "")
    return TrendSkill(
        skill_id,
        str(raw.get("skill_name") or raw.get("raw_skill") or skill_id),
        str(raw.get("category", "未分类")),
        float(raw.get("weight", 0.1)),
        float(raw.get("confidence", 0.9)),
        str(raw.get("importance_level", "edge")),
        float(raw.get("trend_score", 0.0)),
        int(raw.get("evidence_count", 0)),
        str(raw["created_at"]) if raw.get("created_at") else None,
        float(raw["growth_rate"]) if raw.get("growth_rate") is not None else None,
        str(raw["trend_direction"]) if raw.get("trend_direction") is not None else None,
        tuple(str(item) for item in raw.get("evidence_references", ())),
        tuple(str(item) for item in raw.get("quality_flags", ())),
        freeze_json_object(raw["score_explanation"])
        if isinstance(raw.get("score_explanation"), Mapping) else None,
        float(raw["current_window_signal"]) if raw.get("current_window_signal") is not None else None,
        float(raw["historical_window_signal"]) if raw.get("historical_window_signal") is not None else None,
    )


def graph_data(graph: TrendGraphSnapshot) -> dict[str, object]:
    return {
        "position_id": graph.position_id,
        "position_name": graph.position_name,
        "graph_version": graph.graph_version,
        "skills": [skill_data(item) for item in graph.skills],
        "relations": [
            {"source": item.source, "target": item.target, "relation_type": item.relation_type, "weight": item.weight}
            for item in graph.relations
        ],
        "core_responsibilities": list(graph.core_responsibilities),
        "industry_scenarios": list(graph.industry_scenarios),
        "status": graph.status,
    }


def graph_from_data(raw: Mapping[str, object]) -> TrendGraphSnapshot:
    skills = raw.get("skills", [])
    relations = raw.get("relations", [])
    return TrendGraphSnapshot(
        str(raw.get("position_id", "")),
        str(raw.get("position_name", "")),
        str(raw.get("graph_version", "demo_v1")),
        tuple(skill_from_data(item) for item in skills if isinstance(item, Mapping)),
        tuple(
            TrendRelation(str(item.get("source", "")), str(item.get("target", "")), str(item.get("relation_type", "")), float(item.get("weight", 0.0)))
            for item in relations
            if isinstance(item, Mapping)
        ),
        tuple(str(item) for item in raw.get("core_responsibilities", [])),
        tuple(str(item) for item in raw.get("industry_scenarios", [])),
        str(raw.get("status", "existing")),
    )


def distribution_data(value: SkillWeightDistribution) -> dict[str, object]:
    return {name: [skill_data(item) for item in getattr(value, name)] for name in ("core", "high", "bonus", "edge")}


def distribution_from_data(raw: Mapping[str, object]) -> SkillWeightDistribution:
    def group(name: str) -> tuple[TrendSkill, ...]:
        value = raw.get(name, [])
        return tuple(skill_from_data(item) for item in value if isinstance(item, Mapping)) if isinstance(value, list) else ()
    return SkillWeightDistribution(group("core"), group("high"), group("bonus"), group("edge"))


def replacement_data(value: SkillReplacement) -> dict[str, object]:
    return {"declining_skill": skill_data(value.declining_skill), "replacement_skill_name": value.replacement_skill_name, "reason": value.reason}


def replacement_from_data(raw: Mapping[str, object]) -> SkillReplacement:
    declining = raw.get("declining_skill")
    return SkillReplacement(skill_from_data(declining if isinstance(declining, Mapping) else {}), str(raw.get("replacement_skill_name", "")), str(raw.get("reason", "")))


def combo_data(value: SkillComboShift) -> dict[str, object]:
    return {"from_combo": list(value.from_combo), "to_combo": list(value.to_combo), "reason": value.reason}


def combo_from_data(raw: Mapping[str, object]) -> SkillComboShift:
    return SkillComboShift(tuple(str(item) for item in raw.get("from_combo", [])), tuple(str(item) for item in raw.get("to_combo", [])), str(raw.get("reason", "")))


def risk_data(value: TrendRisk) -> dict[str, object]:
    return {"risk_type": value.risk_type, "level": value.level, "reason": value.reason}


def risk_from_data(raw: Mapping[str, object]) -> TrendRisk:
    return TrendRisk(str(raw.get("risk_type", "")), str(raw.get("level", "")), str(raw.get("reason", "")))


def trend_report_data(report: TrendReportRecord, delivery: Mapping[str, object] | None = None) -> dict[str, object]:
    value = {
        "report_id": report.report_id,
        "position_id": report.position_id,
        "graph_version": report.graph_version_id,
        "time_window_start": report.time_window_start.isoformat() if report.time_window_start else None,
        "time_window_end": report.time_window_end.isoformat() if report.time_window_end else None,
        "current_graph": graph_data(report.current_graph),
        "skill_weight_distribution": distribution_data(report.skill_weight_distribution),
        "new_skills": [skill_data(item) for item in report.new_skills],
        "rising_skills": [skill_data(item) for item in report.rising_skills],
        "declining_skills": [skill_data(item) for item in report.declining_skills],
        "replaced_skills": [replacement_data(item) for item in report.replaced_skills],
        "skill_combo_shifts": [combo_data(item) for item in report.skill_combo_shifts],
        "risks": [risk_data(item) for item in report.risks],
        "summary": report.summary,
        "status": report.status,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
        "analysis_mode": "remote_multi_source",
        "provider": "trend_intelligence_http",
        "provider_run_id": report.provider_run_id,
        "algorithm_version": report.algorithm_version,
        "formula_version": report.formula_version,
        "skill_catalog_version": report.skill_catalog_version,
        "source_coverage": report.source_coverage,
        "missing_sources": list(report.missing_sources),
        "quality_flags": list(report.quality_flags),
        "evidence_references": list(report.evidence_references),
        "unresolved_terms": [dict(item) for item in report.unresolved_terms],
        "skill_trends": [
            thaw_json_object(item) for item in report.skill_trend_details
        ],
        "algorithm_result": thaw_json_object(report.algorithm_result)
        if report.algorithm_result else None,
        "reviewed_result": thaw_json_object(report.reviewed_result)
        if report.reviewed_result else None,
        "review_adjustments": [
            thaw_json_object(item) for item in report.review_adjustments
        ],
    }
    value.update(delivery_fields(
        resource_type="trend_report",
        resource_id=report.report_id,
        status=report.status,
        progress=1.0 if report.status == "published" else 0.75,
        source_coverage=report.source_coverage,
        missing_sources=report.missing_sources,
        quality_flags=report.quality_flags,
        evidence_references=report.evidence_references,
        review_status=str(delivery.get("review_status")) if delivery and delivery.get("review_status") else None,
        review_task_id=str(delivery.get("review_task_id")) if delivery and delivery.get("review_task_id") else None,
        publishable=bool(delivery.get("eligible")) if delivery else report.status == "published",
        publication_blockers=delivery.get("blockers", ()) if delivery else (() if report.status == "published" else ("GATE_NOT_EVALUATED",)),
    ))
    return value
