from sqlalchemy import and_, func, or_, select

from app.infrastructure.sqlalchemy.query_base import (
    QuerySession,
    evidence_projection,
    position_build_version,
)
from app.models import (
    AuditLog,
    ExtractedCandidateRequirement,
    ExtractionEvidence,
    GraphBuildRun,
    NormalizedSkillRecord,
    PositionRequirementAggregateDraft,
    PositionSkillRelationDraft,
    PositionSkillSupport,
    PositionTaskAggregateDraft,
    ReviewTask,
    ReviewTaskEvent,
    Skill,
    StandardPosition,
)
from app.application.review_actions import allowed_review_actions


def _relation_values(relation: PositionSkillRelationDraft | None) -> tuple[dict, dict, dict]:
    if relation is None:
        return {}, {}, {}
    original = {
        "weight": relation.auto_weight,
        "confidence": relation.auto_confidence,
        "importance_level": relation.auto_importance_level,
    }
    current = {
        "weight": relation.final_weight,
        "confidence": relation.final_confidence,
        "importance_level": relation.final_importance_level,
        "revision": relation.revision,
        "status": relation.status,
    }
    modified = {
        key: value
        for key, value in {
            "weight": relation.manual_weight,
            "confidence": relation.manual_confidence,
            "importance_level": relation.manual_importance_level,
        }.items()
        if value is not None
    }
    return original, current, modified


def _risk_level(evidence: list[dict], flags: list, relation) -> str:
    if relation is None:
        flag_text = " ".join(str(flag).lower() for flag in flags)
        if any(token in flag_text for token in ("unresolved", "non_exact", "missing")):
            return "high"
        return "medium" if flags else "low"
    if not evidence or any(
        item.get("evidence", {}).get("alignment") != "exact" for item in evidence
    ):
        return "high"
    if flags or (relation is not None and relation.final_confidence < 0.7):
        return "medium"
    return "low"


class ReviewQueryMixin(QuerySession):
    def review_tasks(
        self, *, page: int = 1, page_size: int = 20,
        status: str | None = None, risk_level: str | None = None,
        task_kind: str | None = None,
        build_run_id: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> dict:
        if risk_level is None:
            current_build_ids, zero_relation_build_ids, _ = (
                self._current_build_scope()
            )
            statement = self._review_row_statement(
                status=status,
                task_kind=task_kind,
                build_run_id=build_run_id,
                statuses=statuses,
            )
            statement = statement.where(
                ReviewTask.object_type != "skill_normalization"
            )
            if build_run_id is None:
                statement = statement.where(
                    or_(
                        ReviewTask.build_run_id.is_(None),
                        ReviewTask.build_run_id.in_(current_build_ids),
                    )
                )
                if zero_relation_build_ids:
                    statement = statement.where(
                        ~and_(
                            ReviewTask.object_type == "graph_version",
                            ReviewTask.build_run_id.in_(
                                zero_relation_build_ids
                            ),
                        )
                    )
            total = (
                self.session.scalar(
                    select(func.count()).select_from(
                        statement.order_by(None).subquery()
                    )
                )
                or 0
            )
            start = (page - 1) * page_size
            page_rows = list(
                self.session.scalars(
                    statement.offset(start).limit(page_size)
                ).all()
            )
            lookups = self._build_review_lookups(page_rows)
            result = [self._review_task_item(row, lookups) for row in page_rows]
        else:
            rows = self._select_review_rows(
                status=status,
                task_kind=task_kind,
                build_run_id=build_run_id,
                statuses=statuses,
            )
            lookups = self._build_review_lookups(rows)
            result = [self._review_task_item(row, lookups) for row in rows]
            result = [
                item for item in result if item["risk_level"] == risk_level
            ]
            total = len(result)
            start = (page - 1) * page_size
            result = result[start:start + page_size]
        return {
            "items": result,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def review_task(self, task_id: int) -> dict | None:
        rows = self._select_review_rows(task_id=task_id)
        if not rows:
            return None
        row = rows[0]
        lookups = self._build_review_lookups([row])
        return self._review_task_item(row, lookups)

    def _review_row_statement(
        self, *, status: str | None = None, task_kind: str | None = None,
        task_id: int | None = None, build_run_id: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ):
        statement = select(ReviewTask).order_by(ReviewTask.id)
        if status is not None:
            statement = statement.where(ReviewTask.status == status)
        if statuses:
            statement = statement.where(ReviewTask.status.in_(statuses))
        if task_kind is not None:
            statement = statement.where(ReviewTask.object_type == task_kind)
        if task_id is not None:
            statement = statement.where(ReviewTask.id == task_id)
        if build_run_id is not None:
            statement = statement.where(ReviewTask.build_run_id == build_run_id)
        return statement

    def _select_review_rows(
        self, *, status: str | None = None, task_kind: str | None = None,
        task_id: int | None = None, build_run_id: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ):
        statement = self._review_row_statement(
            status=status,
            task_kind=task_kind,
            task_id=task_id,
            build_run_id=build_run_id,
            statuses=statuses,
        )
        rows = list(self.session.scalars(statement).all())
        current_build_ids, _, builds = self._current_build_scope()

        selected = []
        for row in rows:
            if row.object_type == "skill_normalization":
                continue
            build = builds.get(row.build_run_id) if row.build_run_id else None
            if (
                build_run_id is None
                and build is not None
                and build.id not in current_build_ids
            ):
                continue
            if (
                build_run_id is None
                and
                row.object_type == "graph_version"
                and build is not None
                and int((build.summary or {}).get("relations") or 0) == 0
            ):
                continue
            selected.append(row)
        return selected

    def _current_build_scope(
        self,
    ) -> tuple[set[int], set[int], dict[int, GraphBuildRun]]:
        builds = list(
            self.session.scalars(
                select(GraphBuildRun)
                .where(
                    GraphBuildRun.status.in_(
                        ("succeeded", "published", "draft", "approved")
                    )
                )
                .order_by(GraphBuildRun.id)
            ).all()
        )
        latest_build_by_position: dict[str, int] = {}
        for build in builds:
            if int((build.summary or {}).get("included_samples") or 0) > 0:
                latest_build_by_position[build.position_id] = build.id
        current_build_ids = set(latest_build_by_position.values())
        zero_relation_build_ids = {
            build.id
            for build in builds
            if int((build.summary or {}).get("relations") or 0) == 0
        }
        return (
            current_build_ids,
            zero_relation_build_ids,
            {build.id: build for build in builds},
        )

    def _build_review_lookups(self, rows: list[ReviewTask]) -> dict:
        build_ids = {
            row.build_run_id for row in rows if row.build_run_id is not None
        }
        builds = {}
        if build_ids:
            for build in self.session.scalars(
                select(GraphBuildRun).where(
                    GraphBuildRun.id.in_(build_ids)
                )
            ).all():
                builds[build.id] = build
        relation_rows = []
        aggregate_rows = []
        for row in rows:
            if row.object_type == "position_skill_relation" and row.object_id.isdigit():
                relation_rows.append((row, int(row.object_id)))
            elif row.object_id.isdigit() and row.object_type in (
                "position_requirement", "position_task"
            ):
                aggregate_rows.append((row, int(row.object_id)))

        task_ids = [row.id for row in rows]
        events_by_task: dict[int, list[ReviewTaskEvent]] = {}
        if task_ids:
            for event in self.session.scalars(
                select(ReviewTaskEvent)
                .where(ReviewTaskEvent.task_id.in_(task_ids))
                .order_by(ReviewTaskEvent.id)
            ).all():
                events_by_task.setdefault(event.task_id, []).append(event)

        relations_by_id: dict[int, PositionSkillRelationDraft] = {}
        if relation_rows:
            relation_ids = {object_id for _, object_id in relation_rows}
            for relation in self.session.scalars(
                select(PositionSkillRelationDraft).where(
                    PositionSkillRelationDraft.id.in_(relation_ids)
                )
            ).all():
                relations_by_id[relation.id] = relation
        audits_by_object_id: dict[str, AuditLog] = {}
        if relation_rows:
            object_ids = {str(object_id) for _, object_id in relation_rows}
            for audit in self.session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.object_type == "relation",
                    AuditLog.object_id.in_(object_ids),
                )
                .order_by(AuditLog.id.desc())
            ).all():
                audits_by_object_id.setdefault(audit.object_id, audit)

        aggregates_by_id: dict[int, tuple[str, object]] = {}
        if aggregate_rows:
            requirement_ids = {
                object_id
                for row, object_id in aggregate_rows
                if row.object_type == "position_requirement"
            }
            aggregate_task_ids = {
                object_id
                for row, object_id in aggregate_rows
                if row.object_type == "position_task"
            }
            if requirement_ids:
                for aggregate in self.session.scalars(
                    select(PositionRequirementAggregateDraft).where(
                        PositionRequirementAggregateDraft.id.in_(requirement_ids)
                    )
                ).all():
                    aggregates_by_id[aggregate.id] = ("requirement", aggregate)
            if aggregate_task_ids:
                for aggregate in self.session.scalars(
                    select(PositionTaskAggregateDraft).where(
                        PositionTaskAggregateDraft.id.in_(aggregate_task_ids)
                    )
                ).all():
                    aggregates_by_id[aggregate.id] = ("task", aggregate)

        evidence_by_relation = self._evidence_by_relation(relations_by_id.values())
        evidence_by_aggregate = self._evidence_by_aggregate(
            aggregates_by_id.values()
        )

        position_ids = {build.position_id for build in builds.values()}
        positions_by_id: dict[str, StandardPosition] = {}
        if position_ids:
            for position in self.session.scalars(
                select(StandardPosition).where(
                    StandardPosition.position_id.in_(position_ids)
                )
            ).all():
                positions_by_id[position.position_id] = position
        skill_ids = {relation.skill_id for relation in relations_by_id.values()}
        skills_by_id: dict[str, Skill] = {}
        if skill_ids:
            for skill in self.session.scalars(
                select(Skill).where(Skill.skill_id.in_(skill_ids))
            ).all():
                skills_by_id[skill.skill_id] = skill

        return {
            "builds": builds,
            "events_by_task": events_by_task,
            "relations_by_id": relations_by_id,
            "aggregates_by_id": aggregates_by_id,
            "audits_by_object_id": audits_by_object_id,
            "evidence_by_relation": evidence_by_relation,
            "evidence_by_aggregate": evidence_by_aggregate,
            "positions_by_id": positions_by_id,
            "skills_by_id": skills_by_id,
        }

    def _review_task_item(self, row: ReviewTask, lookups: dict) -> dict:
        build = lookups["builds"].get(row.build_run_id) if row.build_run_id else None
        relation = (
            lookups["relations_by_id"].get(int(row.object_id))
            if row.object_type == "position_skill_relation"
            and row.object_id.isdigit()
            else None
        )
        aggregate = None
        if row.object_id.isdigit() and row.object_type in (
            "position_requirement", "position_task"
        ):
            _, aggregate = lookups["aggregates_by_id"].get(
                int(row.object_id), (None, None)
            )
        events = lookups["events_by_task"].get(row.id, [])
        audit = lookups["audits_by_object_id"].get(row.object_id)
        if relation is not None:
            evidence = lookups["evidence_by_relation"].get(relation.id) or []
        elif aggregate is not None:
            evidence = lookups["evidence_by_aggregate"].get(aggregate.id) or []
        else:
            evidence = []
        payload = dict(row.payload or {})
        position = lookups["positions_by_id"].get(build.position_id) if build is not None else None
        generated_content = aggregate.payload if aggregate is not None else None
        flags = payload.get("review_flags") or payload.get("reasons") or []
        original_values, current_values, manual_values = _relation_values(relation)
        payload_changes = payload.get("changed_content")
        modified_values = (
            manual_values
            if relation is not None
            else (payload_changes if isinstance(payload_changes, dict) else {})
        )
        skill = lookups["skills_by_id"].get(relation.skill_id) if relation is not None else None
        risk = _risk_level(evidence, flags, relation)
        evidence_context = {
            "evidence": evidence,
            "original_values": original_values or payload.get("original_content") or {},
            "current_values": current_values or payload.get("current_content") or {},
            "modified_values": modified_values or payload.get("changed_content") or {},
            "impacted_relations": ([{
                "relation_id": relation.id,
                "skill_id": relation.skill_id,
                "skill_name": skill.canonical_name if skill is not None else relation.skill_id,
                "position_id": relation.position_id,
                "weight": relation.final_weight,
                "confidence": relation.final_confidence,
                "importance_level": relation.final_importance_level,
            }] if relation is not None else []),
            "review_flags": flags,
            "impact_scope": payload.get("impact_scope") or {
                "build_run_id": row.build_run_id,
                "position_id": relation.position_id if relation is not None else None,
            },
            "history": [
                {
                    "id": event.id,
                    "actor_id": event.actor_id,
                    "action": event.action,
                    "before": event.before,
                    "after": event.after,
                    "reason": event.reason,
                    "trace_id": event.trace_id,
                    "created_at": event.created_at,
                }
                for event in events
            ],
        }
        return {
            "contract_version": "review-task.v1",
            "task_id": str(row.id),
            "source_system": "knowledge-graph",
            "task_kind": row.object_type,
            "id": row.id,
            "object_type": row.object_type,
            "object_id": row.object_id,
            "build_run_id": row.build_run_id,
            "build_version": (
                position_build_version(self.session, build) if build is not None else None
            ),
            "position_name": position.name if position is not None else None,
            "build_summary": dict(build.summary or {}) if build is not None else {},
            "status": row.status,
            "assignee_id": row.assignee_id,
            "payload": payload,
            "original_content": payload.get("original_content") or generated_content or (
                audit.before_snapshot if audit is not None else None
            ),
            "changed_content": payload.get("changed_content") or (
                audit.after_snapshot if audit is not None else None
            ),
            "original_values": original_values or payload.get("original_content") or {},
            "current_values": current_values or payload.get("current_content") or {},
            "modified_values": modified_values or payload.get("changed_content") or {},
            "evidence": evidence,
            "review_flags": flags,
            "risk_level": risk,
            "impacted_relations": ([{
                "relation_id": relation.id,
                "skill_id": relation.skill_id,
                "skill_name": skill.canonical_name if skill is not None else relation.skill_id,
                "position_id": relation.position_id,
                "weight": relation.final_weight,
                "confidence": relation.final_confidence,
                "importance_level": relation.final_importance_level,
            }] if relation is not None else []),
            "impact_scope": payload.get("impact_scope") or {
                "build_run_id": row.build_run_id,
                "position_id": relation.position_id if relation is not None else None,
            },
            "history": [
                {
                    "id": event.id,
                    "actor_id": event.actor_id,
                    "action": event.action,
                    "before": event.before,
                    "after": event.after,
                    "reason": event.reason,
                    "trace_id": event.trace_id,
                    "created_at": event.created_at,
                }
                for event in events
            ],
            "allowed_actions": list(allowed_review_actions(row.status)),
            "evidence_context": evidence_context,
            "created_at": row.created_at,
        }

    def _evidence_lookups(self, supports: list[PositionSkillSupport]):
        evidence_ids = {support.evidence_id for support in supports}
        source_ids = {support.source_requirement_id for support in supports}
        normalized_ids = {support.normalized_skill_id for support in supports}
        evidence_by_id = {}
        if evidence_ids:
            evidence_by_id = {
                row.id: row
                for row in self.session.scalars(
                    select(ExtractionEvidence).where(
                        ExtractionEvidence.id.in_(evidence_ids)
                    )
                ).all()
            }
        source_by_id = {}
        if source_ids:
            source_by_id = {
                row.id: row
                for row in self.session.scalars(
                    select(ExtractedCandidateRequirement).where(
                        ExtractedCandidateRequirement.id.in_(source_ids)
                    )
                ).all()
            }
        normalized_by_id = {}
        if normalized_ids:
            normalized_by_id = {
                row.id: row
                for row in self.session.scalars(
                    select(NormalizedSkillRecord).where(
                        NormalizedSkillRecord.id.in_(normalized_ids)
                    )
                ).all()
            }
        return evidence_by_id, source_by_id, normalized_by_id

    def _evidence_by_relation(
        self, relations
    ) -> dict[int, list[dict]]:
        relations = list(relations)
        if not relations:
            return {}
        pairs = {(relation.build_run_id, relation.skill_id) for relation in relations}
        build_run_ids = {pair[0] for pair in pairs}
        skill_ids = {pair[1] for pair in pairs}
        supports = self.session.scalars(
            select(PositionSkillSupport).where(
                PositionSkillSupport.build_run_id.in_(build_run_ids),
                PositionSkillSupport.skill_id.in_(skill_ids),
            )
        ).all()
        supports_by_key: dict[tuple[int, str], list[PositionSkillSupport]] = {}
        for support in supports:
            supports_by_key.setdefault(
                (support.build_run_id, support.skill_id), []
            ).append(support)
        evidence_by_id, source_by_id, normalized_by_id = self._evidence_lookups(
            supports
        )
        result: dict[int, list[dict]] = {}
        for relation in relations:
            items = []
            for support in supports_by_key.get(
                (relation.build_run_id, relation.skill_id), []
            ):
                normalized = normalized_by_id[support.normalized_skill_id]
                items.append(
                    {
                        "support_id": support.id,
                        "document_id": support.document_id,
                        "requirement_id": support.requirement_id,
                        "modality": support.modality,
                        "evidence": evidence_projection(
                            evidence_by_id[support.evidence_id]
                        ),
                        "original_requirement": source_by_id[
                            support.source_requirement_id
                        ].payload,
                        "normalized_skill": {
                            "id": normalized.id,
                            "skill_id": normalized.skill_id,
                            "canonical_name": normalized.canonical_name,
                            "source_name": normalized.source_name,
                            "resolution_status": normalized.resolution_status,
                            "resolution_source": normalized.resolution_source,
                        },
                    }
                )
            result[relation.id] = items
        return result

    def _evidence_by_aggregate(self, aggregates) -> dict[int, list[dict]]:
        aggregates = list(aggregates)
        if not aggregates:
            return {}
        evidence_ids = set()
        for _, aggregate in aggregates:
            evidence_ids.update(
                value
                for value in aggregate.payload.get("evidence_ids", [])
                if isinstance(value, int)
            )
        evidence_by_id = {}
        if evidence_ids:
            evidence_by_id = {
                row.id: row
                for row in self.session.scalars(
                    select(ExtractionEvidence).where(
                        ExtractionEvidence.id.in_(evidence_ids)
                    )
                ).all()
            }
        return {
            aggregate.id: [
                {
                    "evidence_id": value,
                    "evidence": evidence_projection(evidence_by_id[value]),
                }
                for value in aggregate.payload.get("evidence_ids", [])
                if isinstance(value, int)
            ]
            for _, aggregate in aggregates
        }
