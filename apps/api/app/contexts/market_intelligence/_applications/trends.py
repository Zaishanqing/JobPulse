from app.domain.json_types import FrozenJsonObject, freeze_json_object
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Callable
from uuid import uuid4

from app.domain.accounts import AccountActor
from app.contexts.tasks import TaskLog, TaskPayload, TaskRecord, TaskWorkflowPort
from app.contexts.market_intelligence._ports.trends import (
    PositionComparisonProfile,
    PredictedPositionRecord,
    TrendUnitOfWork,
)
from app.contexts.market_intelligence._ports.trend_intelligence_gateway_v1 import (
    CreateMarketPredictionV1,
    TrendIntelligenceGatewayError,
    TrendIntelligenceGatewayV1,
    TrendIntelligenceRunV1,
    TrendPredictionV1,
    TrendSignalV1,
    TrendSourceReportV1,
)
from app.domain.errors import PermissionDenied as PermissionDenied
from app.domain.permissions import (
    TREND_PUBLISH_MANAGE,
    TREND_PUBLISHED_READ,
    TREND_REVIEW_MANAGE,
    TREND_RUN_MANAGE,
    require_permission,
    permissions_for_role,
)
from app.domain.values import thaw


def _terms(values: tuple[str, ...]) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.update(re.findall(r"[a-z0-9+#.]+|[\u4e00-\u9fff]{2,}", value.casefold()))
    return result


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 0.0


class PredictedPositionNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManagePredictedPositions:
    uow_factory: Callable[[], TrendUnitOfWork]
    tasks: TaskWorkflowPort
    gateway: TrendIntelligenceGatewayV1
    algorithm_version: str = "market-prediction-v1"
    formula_version: str = "multi-source-emergence-v1"
    publication_min_source_coverage: float = 0.6
    publication_high_risk_flags: tuple[str, ...] = ("high_risk", "blocking")

    @staticmethod
    def _authorize(actor: AccountActor) -> None:
        require_permission(actor.role, TREND_RUN_MANAGE)

    @staticmethod
    def _ensure_current_relation(history, current, relation_id: str) -> None:
        latest = next(
            (
                item
                for item in history
                if item.relation_identity_id == current.relation_identity_id
            ),
            None,
        )
        if latest is None or latest.relation_id != relation_id or latest.status != "active":
            raise ValueError(
                "Only the latest active relation version can be updated or deleted"
            )

    @staticmethod
    def _prediction_key(prediction: PredictedPositionRecord) -> dict[str, object]:
        return {
            "id": prediction.predicted_id,
            "updated_at": (
                prediction.updated_at.isoformat() if prediction.updated_at else ""
            ),
            "position_name": prediction.position_name,
            "potential_responsibilities": prediction.potential_responsibilities,
            "potential_skills": prediction.potential_skills,
            "industry_scenarios": prediction.industry_scenarios,
            "evidence_references": prediction.evidence_references,
            "prediction_basis": prediction.prediction_basis,
        }

    @staticmethod
    def _profile_key(profile: PositionComparisonProfile) -> tuple[object, ...]:
        return (
            profile.target_type,
            profile.target_id,
            profile.name,
            profile.skill_ids,
            profile.skill_names,
            profile.responsibilities,
            profile.industry_scenarios,
            profile.evidence_references,
        )

    def _digest(self, *values: object) -> str:
        return hashlib.sha256(
            json.dumps(
                values,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _matching_cache_key(
        self,
        prediction: PredictedPositionRecord,
        source: PositionComparisonProfile,
        targets: tuple[PositionComparisonProfile, ...],
        catalog_version: str,
    ) -> str:
        return self._digest(
            self._prediction_key(prediction),
            self._profile_key(source),
            tuple(self._profile_key(item) for item in targets),
            catalog_version,
            prediction.algorithm_version or self.algorithm_version,
            prediction.formula_version or self.formula_version,
            self.algorithm_version,
            self.formula_version,
        )

    def _definition_cache_key(
        self,
        prediction: PredictedPositionRecord,
        matching_key: str,
        catalog_version: str,
    ) -> str:
        return self._digest(
            self._prediction_key(prediction),
            matching_key,
            catalog_version,
            self.algorithm_version,
            self.formula_version,
        )

    def run(
        self,
        actor: AccountActor,
        source_ids: list[str],
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        data_sources: list[str] | None = None,
    ) -> TaskRecord:
        self._authorize(actor)
        now = datetime.now(timezone.utc)
        end = window_end or now
        start = window_start or end - timedelta(days=84)
        task = TaskRecord(
            f"predicted_position_analysis_{uuid4()}",
            "predicted_position_analysis",
            "pending",
            0.0,
            TaskPayload.from_mapping({
                "source_ids": source_ids,
                "time_window": {"start": start.isoformat(), "end": end.isoformat()},
                "data_sources": data_sources or ["arxiv", "cvf", "acl", "policy", "funding", "github"],
            }),
            TaskPayload.from_mapping({
                "provider": self.gateway.provider_name,
                "provider_run_id": None,
                "implementation_status": "remote_run_create_pending",
                "mock": False,
                "rule_based": False,
            }),
            None,
            None,
            None,
            actor.account_id,
            1,
            (TaskLog("pending", now.isoformat(), "Waiting for trend intelligence provider"),),
            now,
            now,
            None,
            None,
        )
        with self.uow_factory() as uow:
            uow.add_task(task)
            uow.commit()

        try:
            remote = self.gateway.create_market_prediction(
                CreateMarketPredictionV1(
                    request_id=task.task_id,
                    idempotency_key=task.task_id,
                    window_start=start,
                    window_end=end,
                    data_sources=tuple(data_sources or ["arxiv", "cvf", "acl", "policy", "funding", "github"]),
                    weights={"policy": 0.25, "academic": 0.25, "funding": 0.25, "github": 0.25},
                    algorithm_version=self.algorithm_version,
                    formula_version=self.formula_version,
                )
            )
        except TrendIntelligenceGatewayError as exc:
            return self._save_provider_error(task.task_id, exc)
        if remote.status == "succeeded":
            self._save_remote_status(
                task.task_id,
                remote,
                status="running",
                error_code=None,
                error_message=None,
            )
        return self._synchronize(task.task_id, remote)

    def task(self, actor: AccountActor, task_id: str) -> TaskRecord:
        self._authorize(actor)
        task = self.tasks.get(actor, task_id, {"predicted_position_analysis"})
        if task.status in {"succeeded", "failed", "cancelled"}:
            return task
        provider_run_id = task.result_payload.get("provider_run_id")
        if not provider_run_id:
            return task
        try:
            remote = self.gateway.get_run(str(provider_run_id))
        except TrendIntelligenceGatewayError as exc:
            return self._save_provider_error(task.task_id, exc)
        return self._synchronize(task.task_id, remote)

    def _synchronize(self, task_id: str, remote: TrendIntelligenceRunV1) -> TaskRecord:
        if remote.status == "succeeded":
            try:
                sources = self.gateway.get_sources(remote.run_id)
                signals = self.gateway.get_signals(remote.run_id)
                predictions = self.gateway.get_predictions(remote.run_id)
            except TrendIntelligenceGatewayError as exc:
                return self._save_provider_error(task_id, exc)
            return self._commit_projection(task_id, remote, sources, signals, predictions)
        if remote.status == "failed":
            return self._save_remote_status(task_id, remote, status="failed", error_code="TREND_INTELLIGENCE_RUN_FAILED", error_message=remote.error_message or "trend intelligence run failed")
        return self._save_remote_status(task_id, remote, status=remote.status, error_code=None, error_message=None)

    def _save_provider_error(self, task_id: str, error: TrendIntelligenceGatewayError) -> TaskRecord:
        remote = TrendIntelligenceRunV1("", "failed", error_message=str(error))
        return self._save_remote_status(task_id, remote, status="failed", error_code=error.code, error_message=str(error))

    def _save_remote_status(self, task_id: str, remote: TrendIntelligenceRunV1, *, status: str, error_code: str | None, error_message: str | None) -> TaskRecord:
        now = datetime.now(timezone.utc)
        with self.uow_factory() as uow:
            current = uow.get_task(task_id)
            if current is None:
                raise LookupError(task_id)
            provider_run_id = remote.run_id or current.result_payload.get("provider_run_id")
            payload = {
                **dict(current.result_payload),
                "provider": "trend_intelligence_http",
                "provider_run_id": provider_run_id,
                "implementation_status": f"remote_multi_source_{status}",
                "remote_status": status,
                "mock": False,
                "rule_based": False,
            }
            updated = replace(
                current,
                status=status,
                progress=0.5 if status == "running" else 0.1 if status == "pending" else 1.0,
                result_payload=TaskPayload.from_mapping(payload),
                error_code=error_code,
                error_message=error_message,
                logs=(*current.logs, TaskLog(status, now.isoformat(), error_message or f"Remote run {status}")),
                updated_at=now,
                started_at=current.started_at or (now if status == "running" else None),
                finished_at=now if status in {"failed", "cancelled"} else None,
            )
            uow.save_task(updated)
            uow.commit()
            return updated

    def _commit_projection(self, task_id: str, remote: TrendIntelligenceRunV1, report: TrendSourceReportV1, signals: tuple[TrendSignalV1, ...], predictions: tuple[TrendPredictionV1, ...]) -> TaskRecord:
        now = datetime.now(timezone.utc)
        with self.uow_factory() as uow:
            current = uow.get_task(task_id)
            if current is None:
                raise LookupError(task_id)
            source_ids: dict[str, str] = {}
            for snapshot in report.snapshots:
                source_type = "policy" if snapshot.source == "policy" else "paper" if snapshot.source in {"arxiv", "cvf", "acl"} else "report"
                projected = uow.sources.add_projection({
                    "source_type": source_type,
                    "title": snapshot.title or f"{snapshot.source} snapshot",
                    "source_name": snapshot.source,
                    "url": snapshot.url,
                    "raw_text": f"Remote snapshot projection: {snapshot.snapshot_id}",
                    "publish_date": snapshot.published_at.date() if snapshot.published_at else None,
                    "credibility_score": 0.8,
                    "parsed_keywords": [],
                    "provider_run_id": remote.run_id,
                    "external_source_id": snapshot.external_id,
                    "source_version": snapshot.source_version,
                    "captured_at": snapshot.captured_at,
                    "snapshot_reference": snapshot.snapshot_id,
                    "extraction_version": ",".join(snapshot.extraction_versions) or None,
                    "source_metadata": dict(snapshot.metadata),
                })
                source_ids[snapshot.snapshot_id] = projected.source_id
            created: list[PredictedPositionRecord] = []
            for prediction in predictions:
                existing = uow.predictions.get_by_provider_candidate(remote.run_id, prediction.candidate_key)
                if existing:
                    created.append(existing)
                    continue
                evidence = [f"trend-intelligence:snapshot:{item}" for item in prediction.evidence_snapshot_ids]
                related = [source_ids[item] for item in prediction.evidence_snapshot_ids if item in source_ids]
                matching_signals = [signal for signal in signals if signal.industry_domain in prediction.industry_domain or prediction.industry_domain in signal.industry_domain]
                created.append(uow.predictions.add({
                    "position_name": prediction.job_name,
                    "prediction_basis": [{"source_scores": dict(prediction.source_scores), "signals": [{"source": item.source, "strength": item.signal_strength, "keywords": list(item.keywords)} for item in matching_signals]}],
                    "related_source_ids": related,
                    "potential_responsibilities": [],
                    "potential_skills": list(prediction.related_keywords),
                    "industry_scenarios": [prediction.industry_domain],
                    "confidence_score": prediction.emergence_score,
                    "status": "candidate",
                    "provider_run_id": remote.run_id,
                    "candidate_key": prediction.candidate_key,
                    "industry_domain": prediction.industry_domain,
                    "emergence_score": prediction.emergence_score,
                    "score_components": dict(prediction.source_scores),
                    "algorithm_version": prediction.algorithm_version,
                    "formula_version": prediction.formula_version,
                    "window_start": prediction.window_start,
                    "window_end": prediction.window_end,
                    "source_coverage": prediction.source_coverage,
                    "missing_sources": list(prediction.missing_sources),
                    "quality_flags": list(prediction.quality_flags),
                    "evidence_references": evidence,
                }))
            payload = {
                "predicted_ids": [item.predicted_id for item in created],
                "source_ids": list(source_ids.values()),
                "provider_run_id": remote.run_id,
                "source_coverage": report.source_coverage,
                "missing_sources": list(report.missing_sources),
                "quality_flags": list(report.quality_flags),
                "note": "predicted_position 来源于远程多源趋势信号，与 emerging_position 的 JD 聚类来源严格区分。",
                "implementation_status": "remote_multi_source_succeeded",
                "provider": "trend_intelligence_http",
                "algorithm_version": predictions[0].algorithm_version if predictions else self.algorithm_version,
                "formula_version": predictions[0].formula_version if predictions else self.formula_version,
                "remote_status": "succeeded",
                "mock": False,
                "rule_based": False,
            }
            updated = replace(current, status="succeeded", progress=1.0, result_payload=TaskPayload.from_mapping(payload), result_reference=f"trend-intelligence:{remote.run_id}", error_code=None, error_message=None, logs=(*current.logs, TaskLog("succeeded", now.isoformat(), "Remote predictions projected atomically")), updated_at=now, started_at=current.started_at or now, finished_at=now)
            uow.save_task(updated)
            uow.commit()
            return updated

    def run_candidate_matching(self, actor: AccountActor, predicted_id: str):
        self._authorize(actor)
        with self.uow_factory() as uow:
            prediction = uow.predictions.get(predicted_id)
            if prediction is None:
                raise PredictedPositionNotFound("Predicted position not found")
            source, targets = uow.predictions.comparison_profiles(predicted_id)
            catalog_version = uow.predictions.skill_catalog_version()
            cache_key = self._matching_cache_key(
                prediction, source, targets, catalog_version
            )
            existing = uow.predictions.list_matches(predicted_id)
            latest_version = max(item.version for item in existing) if existing else 0
            latest = tuple(item for item in existing if item.version == latest_version)
            if latest and latest[0].cache_key == cache_key:
                return latest
            normalized = uow.predictions.normalize_skills(
                source.skill_names,
                context=f"predicted_position:{predicted_id}",
            )
            source_skill_ids = {
                str(item["skill_id"]) for item in normalized if item.get("skill_id")
            } | set(source.skill_ids)
            source_skill_names = {str(item["skill_name"]).casefold() for item in normalized}
            values = []
            for target in targets:
                target_skill_ids = set(target.skill_ids)
                target_skill_names = {name.casefold() for name in target.skill_names}
                matched_ids = source_skill_ids & target_skill_ids
                matched_names = source_skill_names & target_skill_names
                matched = tuple(sorted(matched_ids | matched_names))
                missing = tuple(sorted((target_skill_ids | target_skill_names) - (source_skill_ids | source_skill_names)))
                name_score = _jaccard(_terms((source.name,)), _terms((target.name,)))
                skill_score = _jaccard(
                    source_skill_ids | source_skill_names,
                    target_skill_ids | target_skill_names,
                )
                responsibility_score = _jaccard(
                    _terms(source.responsibilities), _terms(target.responsibilities)
                )
                industry_score = _jaccard(
                    _terms(source.industry_scenarios), _terms(target.industry_scenarios)
                )
                evidence_score = _jaccard(
                    _terms(source.evidence_references),
                    _terms(target.evidence_references),
                )
                score = round(
                    name_score * 0.2 + skill_score * 0.35
                    + responsibility_score * 0.2 + industry_score * 0.15
                    + evidence_score * 0.1,
                    6,
                )
                evidence_count = len(source.evidence_references)
                if evidence_count == 0 or not source.responsibilities or not normalized:
                    recommendation = "insufficient_evidence"
                elif score >= 0.78:
                    recommendation = "possible_duplicate"
                elif score >= 0.45:
                    recommendation = "possible_evolution"
                else:
                    recommendation = "new_candidate"
                values.append({
                    "target_type": target.target_type,
                    "target_id": target.target_id,
                    "similarity_score": score,
                    "matched_skills": list(matched),
                    "missing_skills": list(missing),
                    "overlap_evidence": {
                        "name": name_score,
                        "skills": skill_score,
                        "responsibilities": responsibility_score,
                        "industry_scenarios": industry_score,
                        "trend_evidence": evidence_score,
                        "source_evidence_references": list(source.evidence_references),
                        "target_evidence_references": list(target.evidence_references),
                    },
                    "recommendation": recommendation,
                })
            records = uow.predictions.save_matches(
                predicted_id,
                tuple(values),
                actor.account_id,
                cache_key=cache_key,
            )
            uow.commit()
            return records

    def matching_results(self, actor: AccountActor, predicted_id: str):
        self._authorize(actor)
        with self.uow_factory() as uow:
            if uow.predictions.get(predicted_id) is None:
                raise PredictedPositionNotFound("Predicted position not found")
            return uow.predictions.list_matches(predicted_id)

    def generate_definition(self, actor: AccountActor, predicted_id: str):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            prediction = uow.predictions.get(predicted_id)
            if prediction is None:
                raise PredictedPositionNotFound("Predicted position not found")
            matches = uow.predictions.list_matches(predicted_id)
            matching_key = matches[0].cache_key if matches else "no-matching"
            catalog_version = uow.predictions.skill_catalog_version()
            cache_key = self._definition_cache_key(
                prediction, matching_key, catalog_version
            )
            existing = uow.predictions.list_definitions(predicted_id)
            if existing and existing[0].cache_key == cache_key:
                return existing[0]
            skills = uow.predictions.normalize_skills(
                prediction.potential_skills,
                context=f"predicted_position:{predicted_id}",
            )
            evidence = list(prediction.evidence_references)
            payload = {
                "position_name": prediction.position_name,
                "core_responsibilities": list(prediction.potential_responsibilities),
                "required_skills": [dict(item) for item in skills],
                "bonus_skills": [],
                "industry_scenarios": list(prediction.industry_scenarios),
                "formation_basis": list(prediction.prediction_basis),
                "evidence_by_conclusion": {
                    "position_name": evidence,
                    "core_responsibilities": evidence,
                    "required_skills": {
                        str(item["skill_name"]): evidence for item in skills
                    },
                    "industry_scenarios": evidence,
                    "formation_basis": evidence,
                },
            }
            record = uow.predictions.save_definition(
                predicted_id,
                payload,
                actor.account_id,
                cache_key=cache_key,
            )
            uow.commit()
            return record

    def edit_definition(self, actor: AccountActor, predicted_id: str, definition_id: str, payload):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            current = uow.predictions.get_definition(definition_id)
            if current is None or current.predicted_position_id != predicted_id:
                raise PredictedPositionNotFound("Prediction definition not found")
            if current.status == "published":
                raise ValueError("Published definition versions are immutable")
            merged = {**thaw(current.payload), **thaw(payload)}
            for field in ("required_skills", "bonus_skills"):
                skill_names = tuple(
                    str(
                        (item.get("skill_name") or item.get("name") or item.get("raw_skill"))
                        if isinstance(item, dict) else item
                    )
                    for item in merged.get(field, []) if item
                )
                merged[field] = [
                    dict(item) for item in uow.predictions.normalize_skills(
                        skill_names, context=f"predicted_position:{predicted_id}"
                    )
                ]
            record = uow.predictions.save_definition(
                predicted_id,
                merged,
                actor.account_id,
                cache_key=current.cache_key,
            )
            uow.commit()
            return record

    def definitions(self, actor: AccountActor, predicted_id: str):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            return uow.predictions.list_definitions(predicted_id)

    def submit_definition_review(self, actor: AccountActor, predicted_id: str, definition_id: str, reason: str | None):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            current = uow.predictions.get_definition(definition_id)
            if current is None or current.predicted_position_id != predicted_id:
                raise PredictedPositionNotFound("Prediction definition not found")
            record = uow.predictions.create_definition_review(
                definition_id, actor.account_id, reason
            )
            uow.commit()
            return record

    def definition_review(self, actor: AccountActor, predicted_id: str, definition_id: str):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            current = uow.predictions.get_definition(definition_id)
            if current is None or current.predicted_position_id != predicted_id:
                raise PredictedPositionNotFound("Prediction definition not found")
            return (
                uow.predictions.review_status(current.review_task_id)
                if current.review_task_id else None
            )

    @staticmethod
    def _definition_gate_errors(facts, min_coverage: float, high_risk_flags: tuple[str, ...]):
        result = facts.get("task_result", {})
        definition = facts.get("definition", {})
        errors = []
        if facts.get("task_status") != "succeeded" or not facts.get("provider_run_id"):
            errors.append("REMOTE_ANALYSIS_NOT_SUCCEEDED")
        if result.get("mock") is not False:
            errors.append("MOCK_RESULT_NOT_PUBLISHABLE")
        if float(result.get("source_coverage") or 0) < min_coverage:
            errors.append("SOURCE_COVERAGE_BELOW_THRESHOLD")
        flags = {str(item) for item in result.get("quality_flags", [])}
        if flags & set(high_risk_flags):
            errors.append("UNRESOLVED_HIGH_RISK_FLAGS")
        required = (
            "position_name", "core_responsibilities", "required_skills",
            "industry_scenarios", "formation_basis", "evidence_by_conclusion",
        )
        if any(not definition.get(field) for field in required):
            errors.append("INCOMPLETE_DEFINITION")
        if any(
            item.get("resolution_status") != "resolved" or not item.get("skill_id")
            for item in definition.get("required_skills", [])
        ):
            errors.append("CORE_SKILLS_NOT_NORMALIZED")
        evidence = definition.get("evidence_by_conclusion", {})
        if any(field not in evidence or not evidence.get(field) for field in required[:-1]):
            errors.append("INCOMPLETE_EVIDENCE_REFERENCES")
        if facts.get("review_status") != "approved":
            errors.append("REVIEW_NOT_APPROVED")
        return tuple(errors)

    def publish(self, actor: AccountActor, predicted_id: str, definition_id: str | None = None) -> PredictedPositionRecord:
        require_permission(actor.role, TREND_PUBLISH_MANAGE)
        with self.uow_factory() as uow:
            definitions = uow.predictions.list_definitions(predicted_id)
            selected = (
                next((item for item in definitions if item.definition_id == definition_id), None)
                if definition_id else (definitions[0] if definitions else None)
            )
            if selected is None:
                raise ValueError("Prediction definition is required")
            facts = uow.predictions.publication_facts(predicted_id, selected.definition_id)
            errors = self._definition_gate_errors(
                facts, self.publication_min_source_coverage,
                self.publication_high_risk_flags,
            )
            if errors:
                raise ValueError(";".join(errors))
            record = uow.predictions.publish_definition(
                predicted_id, selected.definition_id, datetime.now(timezone.utc)
            )
            uow.commit()
            return record

    def delivery_status(self, actor: AccountActor, predicted_id: str):
        prediction = self.get(actor, predicted_id)
        with self.uow_factory() as uow:
            definitions = uow.predictions.list_definitions(predicted_id)
            selected = (
                next((item for item in definitions if item.definition_id == prediction.published_definition_version_id), None)
                if prediction.published_definition_version_id else (definitions[0] if definitions else None)
            )
            if selected is None:
                return freeze_json_object({
                    "eligible": False, "blockers": ["DEFINITION_REQUIRED"],
                    "review_status": None, "review_task_id": None,
                })
            facts = uow.predictions.publication_facts(predicted_id, selected.definition_id)
        blockers = () if prediction.status == "published" else self._definition_gate_errors(
            facts, self.publication_min_source_coverage,
            self.publication_high_risk_flags,
        )
        return freeze_json_object({
            "eligible": prediction.status == "published" or not blockers,
            "blockers": list(blockers),
            "review_status": facts.get("review_status"),
            "review_task_id": facts.get("review_task_id"),
        })

    def reject(self, actor: AccountActor, predicted_id: str, definition_id: str):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            definition = uow.predictions.get_definition(definition_id)
            if definition is None or definition.predicted_position_id != predicted_id:
                raise PredictedPositionNotFound("Prediction definition not found")
            review = uow.predictions.review_status(definition.review_task_id) if definition.review_task_id else None
            if not review or review.get("status") != "rejected":
                raise ValueError("Review task must be rejected first")
            record = uow.predictions.reject_definition(definition_id)
            uow.commit()
            return record

    def create_relation(self, actor: AccountActor, predicted_id: str, relation_type: str, target_id: str | None, reason: str | None):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        if relation_type not in {"standard_position", "emerging_position", "independent"}:
            raise ValueError("Unsupported relation type")
        if relation_type != "independent" and not target_id:
            raise ValueError("target_id is required for linked relations")
        with self.uow_factory() as uow:
            record = uow.predictions.save_relation(
                predicted_id,
                relation_type,
                target_id,
                reason,
                actor.account_id,
            )
            uow.commit()
            return record

    def relations(self, actor: AccountActor, predicted_id: str):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            return uow.predictions.list_relations(predicted_id)

    def update_relation(self, actor: AccountActor, predicted_id: str, relation_id: str, relation_type: str, target_id: str | None, reason: str | None):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        if relation_type not in {"standard_position", "emerging_position", "independent"}:
            raise ValueError("Unsupported relation type")
        if relation_type != "independent" and not target_id:
            raise ValueError("target_id is required for linked relations")
        with self.uow_factory() as uow:
            current = uow.predictions.get_relation(relation_id)
            if current is None or current.predicted_position_id != predicted_id:
                raise PredictedPositionNotFound("Prediction relation not found")
            history = uow.predictions.list_relation_history(predicted_id)
            self._ensure_current_relation(history, current, relation_id)
            record = uow.predictions.save_relation(
                predicted_id,
                relation_type,
                target_id,
                reason,
                actor.account_id,
                relation_identity_id=current.relation_identity_id,
                supersedes_relation_id=current.relation_id,
            )
            uow.commit()
            return record

    def delete_relation(self, actor: AccountActor, predicted_id: str, relation_id: str):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            current = uow.predictions.get_relation(relation_id)
            if current is None or current.predicted_position_id != predicted_id:
                raise PredictedPositionNotFound("Prediction relation not found")
            history = uow.predictions.list_relation_history(predicted_id)
            self._ensure_current_relation(history, current, relation_id)
            record = uow.predictions.save_relation(
                predicted_id,
                current.relation_type,
                current.target_id,
                current.reason,
                actor.account_id,
                deleted=True,
                relation_identity_id=current.relation_identity_id,
                supersedes_relation_id=current.relation_id,
            )
            uow.commit()
            return record

    def relation_history(self, actor: AccountActor, predicted_id: str):
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        with self.uow_factory() as uow:
            return uow.predictions.list_relation_history(predicted_id)

    def list(self, actor: AccountActor) -> list[PredictedPositionRecord]:
        permissions = set(permissions_for_role(actor.role))
        if TREND_RUN_MANAGE not in permissions and TREND_PUBLISHED_READ not in permissions:
            require_permission(actor.role, TREND_PUBLISHED_READ)
        with self.uow_factory() as uow:
            records = uow.predictions.list()
            return records if TREND_RUN_MANAGE in permissions else [
                item for item in records if item.status == "published"
            ]

    def get(self, actor: AccountActor, predicted_id: str) -> PredictedPositionRecord:
        permissions = set(permissions_for_role(actor.role))
        if TREND_RUN_MANAGE not in permissions and TREND_PUBLISHED_READ not in permissions:
            require_permission(actor.role, TREND_PUBLISHED_READ)
        with self.uow_factory() as uow:
            record = uow.predictions.get(predicted_id)
            if record is None:
                raise PredictedPositionNotFound("Predicted position not found")
            if TREND_RUN_MANAGE not in permissions and record.status != "published":
                raise PredictedPositionNotFound("Predicted position not found")
            return record

    def update(self, actor: AccountActor, predicted_id: str, changes: FrozenJsonObject) -> PredictedPositionRecord:
        require_permission(actor.role, TREND_REVIEW_MANAGE)
        self.get(actor, predicted_id)
        with self.uow_factory() as uow:
            record = uow.predictions.update(predicted_id, changes)
            uow.commit()
            return record
