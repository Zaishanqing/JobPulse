from dataclasses import dataclass, field, replace
from threading import Lock
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Mapping
from uuid import uuid4

from app.contexts.market_intelligence._ports.position_skill_trend_gateway_v1 import (
    CreatePositionSkillTrendV1,
    PositionSkillTrendGatewayV1,
    PositionSkillTrendRunV1,
)
from app.contexts.market_intelligence._ports.trend_intelligence_gateway_v1 import (
    TrendIntelligenceGatewayError,
)
from app.contexts.market_intelligence._ports.trend_reports import (
    TrendAnalysisUnitOfWork,
    TrendReportChanges,
    TrendReportDraft,
    TrendReportRecord,
)
from app.contexts.tasks import TaskLog, TaskPayload, TaskRecord, TaskWorkflowPort
from app.domain.accounts import AccountActor
from app.domain.json_types import freeze_json_object
from app.domain.permissions import (
    TREND_PUBLISH_MANAGE,
    TREND_PUBLISHED_READ,
    TREND_REVIEW_MANAGE,
    TREND_RUN_MANAGE,
    require_permission,
    permissions_for_role,
)
from app.domain.trend_analysis import (
    SkillComboShift,
    SkillReplacement,
    SkillWeightDistribution,
    TrendGraphSnapshot,
    TrendRelation,
    TrendRisk,
    TrendRuleViolation,
    TrendSkill,
)


class TrendReportNotFound(LookupError):
    pass


class TrendPositionNotFound(LookupError):
    pass


def _utc_boundary(value: date | None, fallback: datetime, *, end: bool = False) -> datetime:
    if value is None:
        return fallback
    return datetime.combine(value, time.max if end else time.min, tzinfo=timezone.utc)


def _graph_payload(graph: TrendGraphSnapshot) -> dict[str, object]:
    return {
        "position_id": graph.position_id,
        "position_name": graph.position_name,
        "graph_version": graph.graph_version,
        "skills": [{
            **item.__dict__,
            "evidence_references": list(item.evidence_references),
            "quality_flags": list(item.quality_flags),
            "score_explanation": dict(item.score_explanation)
            if item.score_explanation else None,
        } for item in graph.skills],
        "relations": [item.__dict__ for item in graph.relations],
        "core_responsibilities": list(graph.core_responsibilities),
        "industry_scenarios": list(graph.industry_scenarios),
        "status": graph.status,
    }


def _graph_from_payload(raw: Mapping[str, object]) -> TrendGraphSnapshot:
    return TrendGraphSnapshot(
        str(raw["position_id"]),
        str(raw["position_name"]),
        str(raw["graph_version"]),
        tuple(
            TrendSkill(**dict(item))
            for item in raw.get("skills", ())
            if isinstance(item, Mapping)
        ),
        tuple(
            TrendRelation(**dict(item))
            for item in raw.get("relations", ())
            if isinstance(item, Mapping)
        ),
        tuple(str(item) for item in raw.get("core_responsibilities", ())),
        tuple(str(item) for item in raw.get("industry_scenarios", ())),
        str(raw.get("status", "existing")),
    )


@dataclass(frozen=True)
class ManageTrendReports:
    uow_factory: Callable[[], TrendAnalysisUnitOfWork]
    tasks: TaskWorkflowPort
    gateway: PositionSkillTrendGatewayV1
    algorithm_version: str = "position-skill-trend-v1"
    formula_version: str = "multi-source-skill-growth-v1"
    config_version: str = "position-skill-trend-config-v1"
    publication_min_source_coverage: float = 0.6
    publication_high_risk_flags: tuple[str, ...] = ("high_risk", "blocking")
    _synchronization_lock: Lock = field(
        default_factory=Lock, init=False, repr=False, compare=False
    )

    def analyze(self, actor: AccountActor, position_id: str, start: date | None, end: date | None) -> TaskRecord:
        require_permission(actor.role, TREND_RUN_MANAGE)
        now = datetime.now(timezone.utc)
        window_end = _utc_boundary(end, now, end=True)
        window_start = _utc_boundary(start, window_end - timedelta(days=84))
        if window_start >= window_end:
            raise TrendRuleViolation("time_window_start must be before time_window_end")
        with self.uow_factory() as uow:
            graph = uow.get_position_graph(position_id)
            if graph is None:
                raise TrendPositionNotFound("Standard position not found")
            if not graph.skills:
                raise TrendRuleViolation(
                    "当前岗位图谱没有标准技能，无法进行能力演化分析。请先构建并发布包含技能关系的岗位图谱。"
                )
            task_id = f"trend_analysis_{uuid4()}"
            graph_version_id = graph.graph_version
            version_graph = graph
            trend_input = uow.position_skill_input(version_graph)
            task = TaskRecord(
                task_id, "trend_analysis", "pending", 0.0,
                TaskPayload.from_mapping({
                    "position_id": position_id,
                    "position_name": version_graph.position_name,
                    "graph_version_id": graph_version_id,
                    "graph_snapshot": _graph_payload(version_graph),
                    "standard_skills": [dict(item) for item in trend_input.standard_skills],
                    "skill_catalog_version": trend_input.skill_catalog_version,
                    "time_window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
                }),
                TaskPayload.from_mapping({
                    "provider": self.gateway.provider_name,
                    "provider_run_id": None,
                    "implementation_status": "remote_run_create_pending",
                    "remote_status": "pending",
                    "mock": False,
                    "rule_based": False,
                }),
                None, None, None, actor.account_id, 1,
                (TaskLog("pending", now.isoformat(), "Waiting for trend intelligence provider"),),
                now, now, None, None,
            )
            uow.add_task(task)
            uow.commit()

        try:
            remote = self.gateway.create_position_skill_trend(CreatePositionSkillTrendV1(
                request_id=task_id,
                idempotency_key=task_id,
                position_id=position_id,
                position_name=version_graph.position_name,
                graph_version_id=graph_version_id,
                standard_skills=trend_input.standard_skills,
                window_start=window_start,
                window_end=window_end,
                skill_catalog_version=trend_input.skill_catalog_version,
                algorithm_version=self.algorithm_version,
                formula_version=self.formula_version,
                config_version=self.config_version,
            ))
        except TrendIntelligenceGatewayError as exc:
            return self._save_provider_error(task_id, exc)
        if remote.status == "succeeded":
            self._save_remote_status(task_id, remote, "running")
        return self._synchronize(task_id, remote)

    def task(self, actor: AccountActor, task_id: str) -> TaskRecord:
        require_permission(actor.role, TREND_RUN_MANAGE)
        task = self.tasks.get(actor, task_id, {"trend_analysis"})
        return self.synchronize_task(task.task_id)

    def synchronize_task(self, task_id: str) -> TaskRecord:
        """Synchronize one task without depending on an active browser request."""
        with self._synchronization_lock:
            with self.uow_factory() as uow:
                task = uow.get_task(task_id)
            if task is None:
                raise LookupError(task_id)
            return self._synchronize_record(task)

    def synchronize_active_tasks(self, limit: int = 50) -> int:
        with self.uow_factory() as uow:
            task_ids = uow.active_task_ids(limit)
        synchronized = 0
        for task_id in task_ids:
            self.synchronize_task(task_id)
            synchronized += 1
        return synchronized

    def _synchronize_record(self, task: TaskRecord) -> TaskRecord:
        if task.status in {"succeeded", "failed", "cancelled"}:
            return task
        run_id = task.result_payload.get("provider_run_id")
        if not run_id:
            return task
        try:
            remote = self.gateway.get_position_skill_trend_run(str(run_id))
        except TrendIntelligenceGatewayError as exc:
            return self._save_provider_error(task.task_id, exc)
        return self._synchronize(task.task_id, remote)

    def _synchronize(self, task_id: str, remote: PositionSkillTrendRunV1) -> TaskRecord:
        if remote.status == "succeeded":
            try:
                result = self.gateway.get_position_skill_trend_result(remote.run_id)
            except TrendIntelligenceGatewayError as exc:
                return self._save_provider_error(task_id, exc)
            return self._commit_projection(task_id, remote, result.payload)
        if remote.status in {"failed", "cancelled"}:
            return self._save_remote_status(
                task_id, remote, remote.status,
                error_code="TREND_INTELLIGENCE_RUN_FAILED" if remote.status == "failed" else None,
                error_message=remote.error_message,
            )
        return self._save_remote_status(task_id, remote, remote.status)

    def _save_provider_error(self, task_id: str, error: TrendIntelligenceGatewayError) -> TaskRecord:
        return self._save_remote_status(
            task_id, PositionSkillTrendRunV1("", "failed", error_message=str(error)), "failed",
            error_code=error.code, error_message=str(error),
        )

    def _save_remote_status(
        self, task_id: str, remote: PositionSkillTrendRunV1, status: str,
        *, error_code: str | None = None, error_message: str | None = None,
    ) -> TaskRecord:
        now = datetime.now(timezone.utc)
        with self.uow_factory() as uow:
            current = uow.get_task(task_id)
            if current is None:
                raise LookupError(task_id)
            run_id = remote.run_id or current.result_payload.get("provider_run_id")
            payload = {**dict(current.result_payload),
                "provider": "trend_intelligence_http",
                "provider_run_id": run_id,
                "implementation_status": f"remote_position_skill_trend_{status}",
                "remote_status": status, "mock": False, "rule_based": False,
            }
            updated = replace(
                current, status=status,
                progress=1.0 if status in {"succeeded", "failed", "cancelled"} else 0.5 if status == "running" else 0.1,
                result_payload=TaskPayload.from_mapping(payload),
                error_code=error_code, error_message=error_message,
                logs=(*current.logs, TaskLog(status, now.isoformat(), error_message)),
                updated_at=now,
                started_at=current.started_at or (now if status != "pending" else None),
                finished_at=now if status in {"succeeded", "failed", "cancelled"} else None,
            )
            uow.save_task(updated)
            uow.commit()
            return updated

    @staticmethod
    def _remote_skills(graph, payload: Mapping[str, object]):
        by_id = {skill.skill_id: skill for skill in graph.skills}
        mapped: dict[str, TrendSkill] = {}
        for raw in payload.get("skill_trends", ()):
            if not isinstance(raw, Mapping) or str(raw.get("skill_id")) not in by_id:
                continue
            base = by_id[str(raw["skill_id"])]
            mapped[base.skill_id] = replace(
                base, trend_score=float(raw.get("trend_score", 0)),
                evidence_count=int(raw.get("evidence_count", 0)),
                confidence=float(raw.get("confidence", base.confidence)),
                growth_rate=float(raw["growth_rate"]) if raw.get("growth_rate") is not None else None,
                trend_direction=str(raw["trend_direction"]) if raw.get("trend_direction") is not None else None,
                evidence_references=tuple(str(item) for item in raw.get("evidence_references", ())),
                quality_flags=tuple(str(item) for item in raw.get("quality_flags", ())),
                score_explanation=freeze_json_object(raw["score_explanation"])
                if isinstance(raw.get("score_explanation"), Mapping) else None,
                current_window_signal=float(raw["current_window_signal"])
                if raw.get("current_window_signal") is not None else None,
                historical_window_signal=float(raw["historical_window_signal"])
                if raw.get("historical_window_signal") is not None else None,
            )
        skills = tuple(mapped.get(item.skill_id, item) for item in graph.skills)
        directions = {
            str(raw.get("skill_id")): str(raw.get("trend_direction"))
            for raw in payload.get("skill_trends", ()) if isinstance(raw, Mapping)
        }
        return skills, directions

    def _commit_projection(self, task_id: str, remote: PositionSkillTrendRunV1, payload) -> TaskRecord:
        now = datetime.now(timezone.utc)
        with self.uow_factory() as uow:
            current = uow.get_task(task_id)
            if current is None:
                raise LookupError(task_id)
            position_id = str(current.input_payload["position_id"])
            graph_version_id = str(current.input_payload["graph_version_id"])
            if str(payload.get("position_id")) != position_id or str(payload.get("graph_version")) != graph_version_id:
                raise TrendRuleViolation("Remote result does not match the immutable analysis input")
            graph_snapshot = current.input_payload.get("graph_snapshot")
            if not isinstance(graph_snapshot, Mapping):
                raise TrendRuleViolation("Immutable graph snapshot is missing")
            version_graph = _graph_from_payload(graph_snapshot)
            version = uow.get_graph_version(position_id, graph_version_id)
            if version is None:
                raise TrendRuleViolation(
                    "The immutable Knowledge Graph version is no longer available"
                )
            version_graph = version.graph
            skills, directions = self._remote_skills(version_graph, payload)
            version_graph = replace(version_graph, skills=skills)
            groups = {name: tuple(item for item in skills if item.importance_level == name) for name in ("core", "high", "bonus", "edge")}
            distribution = SkillWeightDistribution(groups["core"], groups["high"], groups["bonus"], groups["edge"])
            skill_by_id = {item.skill_id: item for item in skills}
            replacements: tuple[SkillReplacement, ...] = ()
            combos = tuple(
                SkillComboShift(
                    tuple(skill_by_id[item].skill_name for item in raw.get("from_skill_ids", ()) if item in skill_by_id),
                    tuple(skill_by_id[item].skill_name for item in raw.get("to_skill_ids", ()) if item in skill_by_id),
                    "Remote multi-source skill combination shift",
                )
                for raw in payload.get("skill_combo_shifts", ()) if isinstance(raw, Mapping)
            )
            flags = tuple(str(item) for item in payload.get("quality_flags", ()))
            unresolved = tuple(
                freeze_json_object(item) for item in payload.get("unresolved_terms", ()) if isinstance(item, Mapping)
            )
            skill_trend_details = tuple(freeze_json_object({
                "skill_id": skill.skill_id,
                "skill_name": skill.skill_name,
                "category": skill.category,
                "weight": skill.weight,
                "importance_level": skill.importance_level,
                "trend_score": skill.trend_score,
                "growth_rate": skill.growth_rate,
                "trend_direction": skill.trend_direction,
                "evidence_count": skill.evidence_count,
                "evidence_references": list(skill.evidence_references),
                "quality_flags": list(skill.quality_flags),
                "score_explanation": dict(skill.score_explanation) if skill.score_explanation else {},
                "current_window_signal": skill.current_window_signal,
                "historical_window_signal": skill.historical_window_signal,
                "confidence": skill.confidence,
            }) for skill in skills)
            risks = tuple(TrendRisk("data_quality", "high" if flag in self.publication_high_risk_flags else "medium", flag) for flag in flags)
            window = current.input_payload["time_window"]
            draft = TrendReportDraft(
                position_id, graph_version_id,
                date.fromisoformat(str(window["start"])[:10]), date.fromisoformat(str(window["end"])[:10]),
                version_graph, distribution,
                tuple(skill for skill in skills if directions.get(skill.skill_id) == "new"),
                tuple(skill for skill in skills if directions.get(skill.skill_id) == "rising"),
                tuple(skill for skill in skills if directions.get(skill.skill_id) == "declining"),
                replacements, combos, risks,
                f"{version_graph.position_name}：已基于多来源数据完成 {len(skills)} 项标准技能趋势分析。",
                remote.run_id,
                str(payload.get("algorithm_version") or self.algorithm_version),
                str(payload.get("formula_version") or self.formula_version),
                str(payload.get("skill_catalog_version") or current.input_payload["skill_catalog_version"]),
                float(payload.get("source_coverage", 0)),
                tuple(str(item) for item in payload.get("missing_sources", ())), flags,
                tuple(str(item) for item in payload.get("evidence_references", ())), unresolved,
                skill_trend_details,
            )
            report = uow.flush_report(draft)
            uow.add_unresolved_terms(remote.run_id, unresolved)
            result_payload = TaskPayload.from_mapping({
                "provider": "trend_intelligence_http", "provider_run_id": remote.run_id,
                "report_id": report.report_id,
                "position_id": position_id, "graph_version_id": graph_version_id,
                "source_coverage": draft.source_coverage,
                "missing_sources": list(draft.missing_sources), "quality_flags": list(flags),
                "evidence_references": list(draft.evidence_references),
                "unresolved_terms": [dict(item) for item in unresolved],
                "skill_trends": [dict(item) for item in skill_trend_details],
                "algorithm_version": draft.algorithm_version,
                "formula_version": draft.formula_version,
                "skill_catalog_version": draft.skill_catalog_version,
                "implementation_status": "remote_position_skill_trend_succeeded",
                "remote_status": "succeeded", "mock": False, "rule_based": False,
            })
            updated = replace(
                current, status="succeeded", progress=1.0, result_payload=result_payload,
                result_reference=f"trend_report:{report.report_id}", error_code=None, error_message=None,
                logs=(*current.logs, TaskLog("succeeded", now.isoformat(), "Remote trend report projected")),
                updated_at=now, started_at=current.started_at or now, finished_at=now,
            )
            uow.save_task(updated)
            uow.commit()
            return updated

    def list_by_position(self, position_id: str, actor: AccountActor | None = None) -> list[TrendReportRecord]:
        with self.uow_factory() as uow:
            if uow.get_position_graph(position_id) is None:
                raise TrendPositionNotFound("Standard position not found")
            records = uow.reports.list_by_position(position_id)
        if actor is None:
            return records
        permissions = set(permissions_for_role(actor.role))
        if permissions & {TREND_RUN_MANAGE, TREND_REVIEW_MANAGE, TREND_PUBLISH_MANAGE}:
            return records
        require_permission(actor.role, TREND_PUBLISHED_READ)
        return [record for record in records if record.status == "published"]

    def get(self, report_id: str, actor: AccountActor | None = None) -> TrendReportRecord:
        with self.uow_factory() as uow:
            record = uow.reports.get(report_id)
        if record is None:
            raise TrendReportNotFound("Trend report not found")
        if actor is not None:
            permissions = set(permissions_for_role(actor.role))
            if not permissions & {TREND_RUN_MANAGE, TREND_REVIEW_MANAGE, TREND_PUBLISH_MANAGE}:
                require_permission(actor.role, TREND_PUBLISHED_READ)
                if record.status != "published":
                    raise TrendReportNotFound("Trend report not found")
        return record

    def update(self, actor: AccountActor, report_id: str, reason: str, changes: TrendReportChanges) -> TrendReportRecord:
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        current = self.get(report_id)
        if current.status == "published":
            raise TrendRuleViolation("Published trend reports are immutable")
        if not reason or not changes.changed_fields:
            raise TrendRuleViolation("Review adjustment requires a reason and at least one changed field")
        with self.uow_factory() as uow:
            record = uow.reports.add_review_adjustment(
                report_id, actor.account_id, reason, changes
            )
            uow.commit()
            return record

    def _publication_gate_failures(self, report: TrendReportRecord, facts) -> tuple[str, ...]:
        result = facts.get("task_result") or {}
        task_input = facts.get("task_input") or {}
        failures = []
        if facts.get("task_status") != "succeeded" or result.get("remote_status") != "succeeded":
            failures.append("REMOTE_ANALYSIS_NOT_SUCCEEDED")
        if result.get("mock") is not False:
            failures.append("MOCK_RESULT_NOT_PUBLISHABLE")
        lineage_matches = (
            result.get("provider") == "trend_intelligence_http"
            and result.get("provider_run_id") == report.provider_run_id
            and result.get("algorithm_version") == report.algorithm_version
            and result.get("formula_version") == report.formula_version
            and result.get("skill_catalog_version") == report.skill_catalog_version
            and float(result.get("source_coverage") or 0) == float(report.source_coverage or 0)
            and tuple(result.get("missing_sources") or ()) == report.missing_sources
            and tuple(result.get("quality_flags") or ()) == report.quality_flags
            and tuple(result.get("evidence_references") or ()) == report.evidence_references
            and tuple(result.get("unresolved_terms") or ()) == tuple(dict(item) for item in report.unresolved_terms)
            and tuple(result.get("skill_trends") or ()) == tuple(dict(item) for item in report.skill_trend_details)
        )
        window = task_input.get("time_window") or {}
        lineage_matches = lineage_matches and (
            task_input.get("graph_version_id") == report.graph_version_id
            and str(window.get("start", ""))[:10] == str(report.time_window_start or "")
            and str(window.get("end", ""))[:10] == str(report.time_window_end or "")
        )
        if not lineage_matches:
            failures.append("ALGORITHM_LINEAGE_MISMATCH")
        if not facts.get("graph_version_exists") or facts.get("graph_position_id") != report.position_id or result.get("graph_version_id") != report.graph_version_id:
            failures.append("GRAPH_VERSION_INPUT_MISMATCH")
        if float(report.source_coverage or 0) < self.publication_min_source_coverage:
            failures.append("SOURCE_COVERAGE_BELOW_THRESHOLD")
        if facts.get("pending_unresolved_terms"):
            failures.append("CORE_SKILLS_NOT_NORMALIZED")
        if set(report.quality_flags) & set(self.publication_high_risk_flags):
            failures.append("UNRESOLVED_HIGH_RISK_FLAGS")
        if facts.get("review_status") != "approved" or facts.get("review_before_latest_adjustment"):
            failures.append("REVIEW_NOT_APPROVED")
        return tuple(failures)

    def delivery_status(self, actor: AccountActor, report_id: str):
        report = self.get(report_id, actor)
        return self.delivery_status_for_record(report)

    def delivery_status_for_record(self, report: TrendReportRecord):
        """Build delivery metadata for an already-authorized report record."""
        with self.uow_factory() as uow:
            facts = uow.report_publication_facts(report.report_id)
        blockers = () if report.status == "published" else self._publication_gate_failures(report, facts)
        return freeze_json_object({
            "eligible": report.status == "published" or not blockers,
            "blockers": list(blockers),
            "review_status": facts.get("review_status"),
            "review_task_id": facts.get("review_task_id"),
        })

    def publish(self, actor: AccountActor, report_id: str) -> TrendReportRecord:
        require_permission(actor.role, TREND_PUBLISH_MANAGE)
        with self.uow_factory() as uow:
            report = uow.reports.get(report_id)
            if report is None:
                raise TrendReportNotFound("Trend report not found")
            if report.status == "published":
                return report
            facts = uow.report_publication_facts(report_id)
            failures = self._publication_gate_failures(report, facts)
            if failures:
                raise TrendRuleViolation(";".join(failures))
            record = uow.reports.update(report_id, TrendReportChanges(frozenset({"status"}), status="published"))
            uow.commit()
            return record
