from collections.abc import Mapping

from sqlalchemy import func, select

from sqlalchemy.orm import Session, sessionmaker

from app.domain.trend_analysis import (
    SkillComboShift,
    SkillReplacement,
    SkillWeightDistribution,
    TrendGraphSnapshot,
    TrendRelation,
    TrendRisk,
    TrendSkill,
)
from app.infrastructure.tasks import SqlAlchemyTaskRepository
from app.models.trend_report import TrendReport, TrendReportReviewAdjustment
from app.models.review_task import ReviewTask
from app.models.skill import Skill
from app.models.skill_alias import SkillAlias
from app.models.skill_normalization_candidate import SkillNormalizationCandidate
from app.models.task_record import TaskRecord as TaskRow
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.contexts.tasks import TaskRecord
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.contexts.market_intelligence import (
    PositionSkillTrendInput,
    TrendGraphVersion,
    TrendReportChanges,
    TrendReportDraft,
    TrendReportRecord,
)


def _skill_data(skill: TrendSkill) -> dict[str, object]:
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


def _skill(raw: Mapping[str, object]) -> TrendSkill:
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


def _graph_data(graph: TrendGraphSnapshot, *, version: str | None = None) -> dict[str, object]:
    return {
        "position_id": graph.position_id,
        "position_name": graph.position_name,
        "graph_version": version or graph.graph_version,
        "skills": [_skill_data(skill) for skill in graph.skills],
        "relations": [
            {"source": item.source, "target": item.target, "relation_type": item.relation_type, "weight": item.weight}
            for item in graph.relations
        ],
        "core_responsibilities": list(graph.core_responsibilities),
        "industry_scenarios": list(graph.industry_scenarios),
        "status": graph.status,
    }


def _graph(raw: Mapping[str, object]) -> TrendGraphSnapshot:
    skills = tuple(_skill(item) for item in raw.get("skills", []) if isinstance(item, Mapping))
    relations = tuple(
        TrendRelation(str(item.get("source", "")), str(item.get("target", "")), str(item.get("relation_type", "")), float(item.get("weight", 0.0)))
        for item in raw.get("relations", [])
        if isinstance(item, Mapping)
    )
    return TrendGraphSnapshot(
        str(raw.get("position_id", "")),
        str(raw.get("position_name", "")),
        str(raw.get("graph_version", "demo_v1")),
        skills,
        relations,
        tuple(str(item) for item in raw.get("core_responsibilities", [])),
        tuple(str(item) for item in raw.get("industry_scenarios", [])),
        str(raw.get("status", "existing")),
    )


def _distribution_data(value: SkillWeightDistribution) -> dict[str, object]:
    return {name: [_skill_data(skill) for skill in getattr(value, name)] for name in ("core", "high", "bonus", "edge")}


def _distribution(raw: Mapping[str, object]) -> SkillWeightDistribution:
    def group(name: str) -> tuple[TrendSkill, ...]:
        value = raw.get(name, [])
        return tuple(_skill(item) for item in value if isinstance(item, Mapping)) if isinstance(value, list) else ()
    return SkillWeightDistribution(group("core"), group("high"), group("bonus"), group("edge"))


def _replacement_data(value: SkillReplacement) -> dict[str, object]:
    return {"declining_skill": _skill_data(value.declining_skill), "replacement_skill_name": value.replacement_skill_name, "reason": value.reason}


def _replacement(raw: Mapping[str, object]) -> SkillReplacement:
    skill = raw.get("declining_skill")
    return SkillReplacement(_skill(skill if isinstance(skill, Mapping) else {}), str(raw.get("replacement_skill_name", "")), str(raw.get("reason", "")))


def _combo_data(value: SkillComboShift) -> dict[str, object]:
    return {"from_combo": list(value.from_combo), "to_combo": list(value.to_combo), "reason": value.reason}


def _combo(raw: Mapping[str, object]) -> SkillComboShift:
    return SkillComboShift(tuple(str(item) for item in raw.get("from_combo", [])), tuple(str(item) for item in raw.get("to_combo", [])), str(raw.get("reason", "")))


def _risk_data(value: TrendRisk) -> dict[str, object]:
    return {"risk_type": value.risk_type, "level": value.level, "reason": value.reason}


def _risk(raw: Mapping[str, object]) -> TrendRisk:
    return TrendRisk(str(raw.get("risk_type", "")), str(raw.get("level", "")), str(raw.get("reason", "")))


class SqlAlchemyTrendReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: TrendReportDraft) -> TrendReportRecord:
        row = TrendReport(
            position_id=draft.position_id,
            graph_version_id=draft.graph_version_id,
            time_window_start=draft.time_window_start,
            time_window_end=draft.time_window_end,
            current_graph=_graph_data(draft.current_graph),
            skill_weight_distribution=_distribution_data(draft.skill_weight_distribution),
            new_skills=[_skill_data(item) for item in draft.new_skills],
            rising_skills=[_skill_data(item) for item in draft.rising_skills],
            declining_skills=[_skill_data(item) for item in draft.declining_skills],
            replaced_skills=[_replacement_data(item) for item in draft.replaced_skills],
            skill_combo_shifts=[_combo_data(item) for item in draft.skill_combo_shifts],
            risks=[_risk_data(item) for item in draft.risks],
            summary=draft.summary,
            provider_run_id=draft.provider_run_id,
            algorithm_version=draft.algorithm_version,
            formula_version=draft.formula_version,
            skill_catalog_version=draft.skill_catalog_version,
            source_coverage=draft.source_coverage,
            missing_sources=list(draft.missing_sources),
            quality_flags=list(draft.quality_flags),
            evidence_references=list(draft.evidence_references),
            unresolved_terms=[dict(item) for item in draft.unresolved_terms],
            skill_trend_details=[
                thaw_json_object(item) for item in draft.skill_trend_details
            ],
            status="draft",
        )
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def get(self, report_id: str) -> TrendReportRecord | None:
        row = self._session.get(TrendReport, report_id)
        return self._record(row) if row is not None else None

    def list_by_position(self, position_id: str) -> list[TrendReportRecord]:
        rows = self._session.query(TrendReport).filter(TrendReport.position_id == position_id).order_by(TrendReport.created_at.desc()).all()
        return [self._record(row) for row in rows]

    def get_by_provider(self, provider_run_id: str, position_id: str, graph_version_id: str) -> TrendReportRecord | None:
        row = self._session.query(TrendReport).filter(
            TrendReport.provider_run_id == provider_run_id,
            TrendReport.position_id == position_id,
            TrendReport.graph_version_id == graph_version_id,
        ).one_or_none()
        return self._record(row) if row is not None else None

    def update(self, report_id: str, changes: TrendReportChanges) -> TrendReportRecord:
        row = self._session.get(TrendReport, report_id)
        if row is None:
            raise LookupError(report_id)
        converters = {
            "current_graph": _graph_data,
            "skill_weight_distribution": _distribution_data,
            "new_skills": lambda values: [_skill_data(item) for item in values],
            "rising_skills": lambda values: [_skill_data(item) for item in values],
            "declining_skills": lambda values: [_skill_data(item) for item in values],
            "replaced_skills": lambda values: [_replacement_data(item) for item in values],
            "skill_combo_shifts": lambda values: [_combo_data(item) for item in values],
            "risks": lambda values: [_risk_data(item) for item in values],
        }
        for name in changes.changed_fields:
            value = getattr(changes, name)
            if value is not None and name in converters:
                value = converters[name](value)
            setattr(row, name, value)
        self._session.flush()
        return self._record(row)

    @staticmethod
    def _change_values(changes: TrendReportChanges) -> dict[str, object]:
        converters = {
            "current_graph": _graph_data,
            "skill_weight_distribution": _distribution_data,
            "new_skills": lambda values: [_skill_data(item) for item in values],
            "rising_skills": lambda values: [_skill_data(item) for item in values],
            "declining_skills": lambda values: [_skill_data(item) for item in values],
            "replaced_skills": lambda values: [_replacement_data(item) for item in values],
            "skill_combo_shifts": lambda values: [_combo_data(item) for item in values],
            "risks": lambda values: [_risk_data(item) for item in values],
        }
        values: dict[str, object] = {}
        for name in changes.changed_fields:
            value = getattr(changes, name)
            values[name] = converters[name](value) if value is not None and name in converters else value
        return values

    def add_review_adjustment(
        self,
        report_id: str,
        actor_id: str,
        reason: str,
        changes: TrendReportChanges,
    ) -> TrendReportRecord:
        row = self._session.get(TrendReport, report_id)
        if row is None:
            raise LookupError(report_id)
        _, reviewed, _ = self._result_payloads(row)
        after = self._change_values(changes)
        before = {name: reviewed.get(name) for name in after}
        self._session.add(TrendReportReviewAdjustment(
            report_id=report_id,
            actor_user_id=actor_id,
            reason=reason,
            before_values=before,
            after_values=after,
        ))
        self._session.flush()
        return self._record(row)

    def _result_payloads(self, row: TrendReport):
        algorithm = {
            "time_window_start": row.time_window_start.isoformat() if row.time_window_start else None,
            "time_window_end": row.time_window_end.isoformat() if row.time_window_end else None,
            "graph_version": row.graph_version_id,
            "provider_run_id": row.provider_run_id,
            "algorithm_version": row.algorithm_version,
            "formula_version": row.formula_version,
            "skill_catalog_version": row.skill_catalog_version,
            "source_coverage": row.source_coverage,
            "missing_sources": list(row.missing_sources or ()),
            "quality_flags": list(row.quality_flags or ()),
            "evidence_references": list(row.evidence_references or ()),
            "unresolved_terms": list(row.unresolved_terms or ()),
            "skill_trends": list(row.skill_trend_details or ()),
            "current_graph": row.current_graph or {},
            "skill_weight_distribution": row.skill_weight_distribution or {},
            "new_skills": list(row.new_skills or ()),
            "rising_skills": list(row.rising_skills or ()),
            "declining_skills": list(row.declining_skills or ()),
            "replaced_skills": list(row.replaced_skills or ()),
            "skill_combo_shifts": list(row.skill_combo_shifts or ()),
            "risks": list(row.risks or ()),
            "summary": row.summary,
        }
        reviewed = dict(algorithm)
        rows = self._session.query(TrendReportReviewAdjustment).filter(
            TrendReportReviewAdjustment.report_id == row.id
        ).order_by(
            TrendReportReviewAdjustment.created_at.asc(),
            TrendReportReviewAdjustment.id.asc(),
        ).all()
        audit = []
        for adjustment in rows:
            reviewed.update(adjustment.after_values or {})
            audit.append({
                "adjustment_id": adjustment.id,
                "actor_user_id": adjustment.actor_user_id,
                "reason": adjustment.reason,
                "before_values": adjustment.before_values or {},
                "after_values": adjustment.after_values or {},
                "created_at": adjustment.created_at.isoformat() if adjustment.created_at else None,
            })
        return algorithm, reviewed, audit

    def _record(self, row: TrendReport) -> TrendReportRecord:
        algorithm, reviewed, audit = self._result_payloads(row)
        return TrendReportRecord(
            row.id,
            row.position_id,
            row.graph_version_id,
            row.time_window_start,
            row.time_window_end,
            _graph(reviewed["current_graph"]),
            _distribution(reviewed["skill_weight_distribution"]),
            tuple(_skill(item) for item in reviewed["new_skills"]),
            tuple(_skill(item) for item in reviewed["rising_skills"]),
            tuple(_skill(item) for item in reviewed["declining_skills"]),
            tuple(_replacement(item) for item in reviewed["replaced_skills"]),
            tuple(_combo(item) for item in reviewed["skill_combo_shifts"]),
            tuple(_risk(item) for item in reviewed["risks"]),
            reviewed["summary"],
            row.status,
            row.created_at,
            row.updated_at,
            row.provider_run_id,
            row.algorithm_version,
            row.formula_version,
            row.skill_catalog_version,
            row.source_coverage,
            tuple(row.missing_sources or ()),
            tuple(row.quality_flags or ()),
            tuple(row.evidence_references or ()),
            tuple(freeze_json_object(item) for item in row.unresolved_terms or ()),
            tuple(freeze_json_object(item) for item in row.skill_trend_details or ()),
            freeze_json_object(algorithm),
            freeze_json_object(reviewed),
            tuple(freeze_json_object(item) for item in audit),
        )


def _flush_trend_report(repository: SqlAlchemyTrendReportRepository, draft: TrendReportDraft) -> TrendReportRecord:
    return repository.add(draft)


def create_succeeded_task(repository: SqlAlchemyTaskRepository, task: TaskRecord) -> None:
    repository.add(task)


class SqlAlchemyTrendAnalysisUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session], knowledge_graph_client) -> None:
        self._session_factory = session_factory
        self._knowledge_graph_client = knowledge_graph_client
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyTrendAnalysisUnitOfWork":
        self._session = self._session_factory()
        self.reports = SqlAlchemyTrendReportRepository(self._session)
        self._tasks = SqlAlchemyTaskRepository(self._session)
        return self

    def get_position_graph(self, position_id: str) -> TrendGraphSnapshot | None:
        knowledge_graph_position_id = self._knowledge_graph_position_id(position_id)
        if knowledge_graph_position_id is None:
            return None
        profile = self._knowledge_graph_client.position_profile(
            knowledge_graph_position_id
        ).data
        self._register_profile_reference(profile, position_id)
        return self._kg_graph(profile, position_id=position_id)

    def get_graph_version(self, position_id: str, version_id: str) -> TrendGraphVersion | None:
        knowledge_graph_position_id = self._knowledge_graph_position_id(position_id)
        if knowledge_graph_position_id is None:
            return None
        try:
            profile = self._knowledge_graph_client.position_profile(
                knowledge_graph_position_id,
                graph_version_id=int(version_id),
            ).data
        except (TypeError, ValueError):
            return None
        self._register_profile_reference(profile, position_id)
        graph = self._kg_graph(profile, position_id=position_id)
        return TrendGraphVersion(version_id, graph) if graph is not None else None

    def _knowledge_graph_position_id(self, position_id: str) -> str | None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        mapping = self._session.scalar(
            select(KnowledgeGraphEntityMapping).where(
                KnowledgeGraphEntityMapping.entity_type == "position",
                KnowledgeGraphEntityMapping.main_system_id == position_id,
                KnowledgeGraphEntityMapping.sync_status.in_(("synced", "confirmed")),
            )
        )
        if mapping is None or not mapping.knowledge_graph_id:
            return None
        return str(mapping.knowledge_graph_id)

    def _register_profile_reference(self, profile, position_id: str) -> None:
        if not isinstance(profile, Mapping) or profile.get("graph_version_id") is None:
            return
        self._knowledge_graph_client.register_dependency_reference(
            consumer_system="trend",
            reference_type="position-profile-input",
            reference_id=position_id,
            graph_version_id=int(profile["graph_version_id"]),
            metadata={"contract_version": profile.get("contract_version")},
        )

    @staticmethod
    def _kg_graph(
        profile, *, position_id: str | None = None
    ) -> TrendGraphSnapshot | None:
        if not isinstance(profile, Mapping) or profile.get("profile_state") != "published":
            return None
        evidence_by_skill: dict[str, list[str]] = {}
        for item in profile.get("evidence_summary", ()):
            if isinstance(item, Mapping) and item.get("skill_id") is not None:
                evidence_by_skill.setdefault(str(item["skill_id"]), []).append(
                    str(item["evidence_id"])
                )
        skills = tuple(
            TrendSkill(
                str(item["skill_id"]),
                str(item["skill_name"]),
                str(item.get("category_code") or "未分类"),
                float(item["weight"]),
                float(item["confidence"]),
                str(item["importance_level"]),
                0.0,
                int(item.get("evidence_count", 0)),
                evidence_references=tuple(
                    evidence_by_skill.get(str(item["skill_id"]), ())
                ),
            )
            for item in profile.get("skill_relations", ())
            if isinstance(item, Mapping)
        )
        resolved_position_id = position_id or str(profile["position_id"])
        return TrendGraphSnapshot(
            resolved_position_id,
            str(profile.get("position_name") or resolved_position_id),
            str(profile["graph_version_id"]),
            skills,
            tuple(
                TrendRelation(
                    resolved_position_id, skill.skill_id, "requires", skill.weight
                )
                for skill in skills
            ),
            tuple(
                str(item.get("text") or "")
                for item in profile.get("responsibilities", ())
                if isinstance(item, Mapping)
            ),
            (),
            "existing",
        )

    def position_skill_input(self, graph: TrendGraphSnapshot) -> PositionSkillTrendInput:
        skill_ids = tuple(skill.skill_id for skill in graph.skills)
        skills = {
            row.id: row for row in self._session.query(Skill).filter(Skill.id.in_(skill_ids)).all()
        } if skill_ids else {}
        aliases: dict[str, list[str]] = {skill_id: [] for skill_id in skill_ids}
        if skill_ids:
            for row in self._session.query(SkillAlias).filter(SkillAlias.skill_id.in_(skill_ids)).all():
                aliases.setdefault(row.skill_id, []).append(row.alias)
        values = tuple(freeze_json_object({
            "skill_id": item.skill_id,
            "skill_name": skills[item.skill_id].skill_name if item.skill_id in skills else item.skill_name,
            "aliases": sorted(aliases.get(item.skill_id, ())),
        }) for item in graph.skills)
        catalog_version = "skill-catalog-current"
        return PositionSkillTrendInput(graph, values, catalog_version)

    def flush_report(self, draft: TrendReportDraft) -> TrendReportRecord:
        existing = self.reports.get_by_provider(
            draft.provider_run_id, draft.position_id, draft.graph_version_id
        )
        return existing or _flush_trend_report(self.reports, draft)

    def add_task(self, task: TaskRecord) -> None:
        create_succeeded_task(self._tasks, task)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def active_task_ids(self, limit: int = 50) -> tuple[str, ...]:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        return tuple(self._session.scalars(
            select(TaskRow.id).where(
                TaskRow.task_type == "trend_analysis",
                TaskRow.status.in_(("pending", "running")),
                TaskRow.result_payload["provider_run_id"].as_string().is_not(None),
            ).order_by(TaskRow.updated_at.asc(), TaskRow.id.asc()).limit(limit)
        ))

    def save_task(self, task: TaskRecord) -> None:
        self._tasks.save(task)

    def add_unresolved_terms(self, provider_run_id: str, terms) -> None:
        context = f"trend_report:{provider_run_id}"
        # The remote report keeps every unresolved expression for audit, while
        # this table is only the skill-normalization work queue. Reject values
        # outside its persisted contract and load existing rows in bounded bulk
        # queries instead of issuing one SELECT per expression.
        names = {
            name.casefold(): name
            for item in terms
            if (name := str(item.get("term") or "").strip()) and len(name) <= 128
        }
        existing: set[str] = set()
        keys = list(names)
        for start in range(0, len(keys), 500):
            existing.update(self._session.scalars(
                select(func.lower(SkillNormalizationCandidate.raw_skill)).where(
                    SkillNormalizationCandidate.context == context,
                    func.lower(SkillNormalizationCandidate.raw_skill).in_(
                        keys[start:start + 500]
                    ),
                )
            ))
        self._session.add_all(
            SkillNormalizationCandidate(
                raw_skill=name,
                candidate_skill_id=None,
                confidence=0.0,
                context=context,
                status="pending",
            )
            for key, name in names.items()
            if key not in existing
        )

    def report_publication_facts(self, report_id: str):
        report = self._session.get(TrendReport, report_id)
        if report is None:
            raise LookupError(report_id)
        task = next((row for row in self._session.query(TaskRow).filter(
            TaskRow.task_type == "trend_analysis"
        ).all() if (row.result_payload or {}).get("provider_run_id") == report.provider_run_id), None)
        graph_profile = None
        if report.graph_version_id:
            knowledge_graph_position_id = self._knowledge_graph_position_id(
                report.position_id
            )
            try:
                if knowledge_graph_position_id is not None:
                    graph_profile = self._knowledge_graph_client.position_profile(
                        knowledge_graph_position_id,
                        graph_version_id=int(report.graph_version_id),
                    ).data
            except (TypeError, ValueError):
                graph_profile = None
        review = self._session.query(ReviewTask).filter(
            ReviewTask.object_type == "trend_report", ReviewTask.object_id == report_id,
        ).order_by(ReviewTask.created_at.desc()).first()
        pending_terms = self._session.query(SkillNormalizationCandidate).filter(
            SkillNormalizationCandidate.context == f"trend_report:{report.provider_run_id}",
            SkillNormalizationCandidate.status == "pending",
        ).count()
        return freeze_json_object({
            "task_status": task.status if task else None,
            "task_result": task.result_payload if task else {},
            "task_input": task.input_payload if task else {},
            "graph_version_exists": isinstance(graph_profile, Mapping),
            "graph_position_id": (
                report.position_id
                if isinstance(graph_profile, Mapping)
                else None
            ),
            "pending_unresolved_terms": pending_terms,
            "review_status": review.status if review else None,
            "review_task_id": review.id if review else None,
            "review_before_latest_adjustment": bool(
                review
                and (latest_adjustment := self._session.query(
                    TrendReportReviewAdjustment
                ).filter(
                    TrendReportReviewAdjustment.report_id == report_id
                ).order_by(
                    TrendReportReviewAdjustment.created_at.desc(),
                    TrendReportReviewAdjustment.id.desc(),
                ).first())
                and review.updated_at < latest_adjustment.created_at
            ),
        })

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
