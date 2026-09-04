from sqlalchemy import select

from app.application.mappers import GraphSnapshotCompatibilityMapper
from app.infrastructure.sqlalchemy.graph_persistence import (
    _snapshot,
    build_summary_status,
    relation_explanation as build_relation_explanation,
)
from app.infrastructure.sqlalchemy.query_base import (
    QuerySession,
    compact_graph_snapshot,
    position_build_version,
)
from app.models import AuditLog, GraphBuildRun, GraphVersion, PositionSkillRelationDraft, StandardPosition


def _relation_statistics(item: dict) -> dict:
    metrics = item.get("metrics") or {}
    stored = item.get("statistics") or {}
    return {
        "supporting_jd_count": int(
            stored.get("supporting_jd_count", metrics.get("support_count", 0))
        ),
        "deduplicated_jd_count": int(
            stored.get(
                "deduplicated_jd_count",
                metrics.get("support_document_count", 0),
            )
        ),
        "enterprise_count": int(
            stored.get("enterprise_count", metrics.get("enterprise_coverage", 0))
        ),
        "source_count": int(
            stored.get("source_count", metrics.get("source_diversity", 0))
        ),
        "evidence_count": int(
            stored.get("evidence_count", metrics.get("support_count", 0))
        ),
        "first_seen_at": stored.get("first_seen_at"),
        "last_seen_at": stored.get("last_seen_at"),
        "raw_frequency": float(
            stored.get("raw_frequency", metrics.get("support_ratio", 0))
        ),
        "quality_adjusted_frequency": float(
            stored.get(
                "quality_adjusted_frequency",
                metrics.get("weighted_frequency", 0),
            )
        ),
    }


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def published_graph_snapshot(session, version: GraphVersion) -> dict:
    """Normalize a stored snapshot into the unified portal GraphSnapshot contract.

    A-DATA-01 frozen releases predate the unified contract and store legacy
    fields (sample_count/skill_count/position_name). Derive the missing
    position and sample_stats from the catalog and build-run records so portal
    consumers never receive a partial snapshot.
    """
    current = compact_graph_snapshot(version.snapshot)
    current["view_type"] = "published"
    current["version_id"] = version.id
    current["build_run_id"] = version.build_run_id
    current["skills"] = current.get("skill_relations", [])
    current["task_profile"] = current.get("responsibilities", [])
    run = session.get(GraphBuildRun, version.build_run_id)
    if "position" not in current:
        position = session.scalar(
            select(StandardPosition).where(
                StandardPosition.position_id == version.position_id
            )
        )
        if position is not None:
            current["position"] = {
                "position_id": position.position_id,
                "name": position.name,
                "category_code": position.category_code,
            }
    if "sample_stats" not in current:
        summary = build_summary_status(session, run) if run is not None else {}
        current["sample_stats"] = {
            "included_samples": _as_int(
                summary.get("included_samples", summary.get("sample_count"))
            ),
            "excluded_samples": _as_int(summary.get("excluded_samples")),
            "relations": _as_int(
                summary.get(
                    "relations",
                    summary.get(
                        "skill_count", len(current.get("skill_relations") or [])
                    ),
                )
            ),
            "minimum_valid_samples": _as_int(
                summary.get("minimum_valid_samples"), 1
            ),
        }
    if run is not None:
        base_version = (
            session.get(GraphVersion, run.base_version_id)
            if run.base_version_id is not None
            else None
        )
        base_build_run = (
            session.get(GraphBuildRun, base_version.build_run_id)
            if base_version is not None
            else None
        )
        current["base_version_id"] = run.base_version_id
        current["build_info"] = {
            "build_run_id": run.id,
            "build_version": position_build_version(session, run),
            "base_build_version": (
                position_build_version(session, base_build_run)
                if base_build_run is not None
                else None
            ),
            "status": run.status,
            "window_start": run.window_start,
            "window_end": run.window_end,
            "config_snapshot": run.config_snapshot,
            "summary": build_summary_status(session, run),
            "created_at": run.created_at,
        }
    for key in (
        "requirement_profile",
        "company_context",
        "employment_context",
        "evidence_summary",
    ):
        current.setdefault(key, [])
    return current


class GraphQueryMixin(QuerySession):
    def graph(self, position_id: str) -> dict:
        version = self.current_version(position_id)
        if version is None:
            position = self.session.scalar(
                select(StandardPosition).where(StandardPosition.position_id == position_id)
            )
            if position is None:
                raise LookupError(f"Position not found: {position_id}")
            return {
                "position_id": position_id,
                "position": {
                    "position_id": position_id,
                    "name": position.name,
                    "category_code": position.category_code,
                },
                "sample_stats": {
                    "included_samples": 0,
                    "excluded_samples": 0,
                    "relations": 0,
                    "minimum_valid_samples": 1,
                },
                "skill_relations": [],
                "skills": [],
                "requirement_profile": [],
                "responsibilities": [],
                "company_context": [],
                "employment_context": [],
                "task_profile": [],
                "view_type": "published",
                "version_id": None,
                "warning": "该岗位尚未发布正式图谱版本",
            }
        return published_graph_snapshot(self.session, version)

    def draft_graph(self, run_id: int) -> dict | None:
        run = self.session.get(GraphBuildRun, run_id)
        if run is None:
            return None
        if self.session.scalar(select(GraphVersion.id).where(GraphVersion.build_run_id == run_id)) is not None:
            return None
        relations = self.session.scalars(
            select(PositionSkillRelationDraft).where(
                PositionSkillRelationDraft.build_run_id == run_id
            )
        ).all()
        current = _snapshot(
            self.session,
            run,
            relations,
            include_explanation=False,
            include_evidence_summary=False,
        )
        current["skills"] = current.get("skill_relations", [])
        current["task_profile"] = current.get("responsibilities", [])
        current["view_type"] = "draft"
        current["draft_id"] = run.id
        current["build_run_id"] = run.id
        current["base_version_id"] = run.base_version_id
        current["build_info"] = {
            "build_run_id": run.id,
            "build_version": position_build_version(self.session, run),
            "status": run.status,
            "window_start": run.window_start,
            "window_end": run.window_end,
            "config_snapshot": run.config_snapshot,
            "summary": build_summary_status(self.session, run),
            "created_at": run.created_at,
        }
        relation_ids = [str(relation.id) for relation in relations]
        histories: dict[str, list[dict]] = {}
        if relation_ids:
            audit_events = self.session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.object_type == "relation",
                    AuditLog.object_id.in_(relation_ids),
                )
                .order_by(AuditLog.id)
            ).all()
            for event in audit_events:
                histories.setdefault(event.object_id, []).append(
                    {
                        "id": event.id,
                        "actor_id": event.actor_id,
                        "before": event.before_snapshot,
                        "after": event.after_snapshot,
                        "reason": event.reason,
                        "trace_id": event.trace_id,
                        "created_at": event.created_at,
                    }
                )
        for relation in current.get("skill_relations", []):
            relation["modification_history"] = histories.get(
                str(relation.get("relation_id")), []
            )
        return current

    def relations(
        self,
        position_id: str,
        *,
        version_id: int | None,
        page: int,
        page_size: int,
        skill_id: str | None,
        category_code: str | None,
        importance_level: str | None,
        modality: str | None,
        min_weight: float | None,
        min_confidence: float | None,
    ) -> dict | None:
        current_version = self.current_version(position_id)
        version = (
            self.session.get(GraphVersion, version_id)
            if version_id is not None else current_version
        )
        if version_id is not None and (
            version is None or version.position_id != position_id
        ):
            return None
        snapshot = (
            compact_graph_snapshot(version.snapshot)
            if version is not None else {"skill_relations": []}
        )
        items = [
            {**item, "statistics": _relation_statistics(item)}
            for item in snapshot.get("skill_relations", [])
        ]
        if skill_id is not None:
            items = [item for item in items if item.get("skill_id") == skill_id]
        if category_code is not None:
            items = [
                item for item in items
                if item.get("category_code") == category_code
            ]
        if importance_level is not None:
            items = [
                item for item in items
                if item.get("final_importance_level", item.get("importance_level"))
                == importance_level
            ]
        if modality is not None:
            items = [
                item for item in items
                if item.get("primary_modality") == modality
                or float(
                    (item.get("modality_distribution") or {}).get(modality, 0)
                ) > 0
            ]
        if min_weight is not None:
            items = [
                item for item in items
                if float(item.get("final_weight", item.get("weight", 0)))
                >= min_weight
            ]
        if min_confidence is not None:
            items = [
                item for item in items
                if float(
                    item.get("final_confidence", item.get("confidence", 0))
                ) >= min_confidence
            ]
        items.sort(
            key=lambda item: (
                -float(item.get("final_weight", item.get("weight", 0))),
                str(item.get("skill_id", "")),
            )
        )
        total = len(items)
        start = (page - 1) * page_size
        return {
            "position_id": position_id,
            "version_id": version.id if version is not None else None,
            "is_current": (
                version is not None
                and current_version is not None
                and version.id == current_version.id
            ),
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items[start:start + page_size],
        }

    def relation_explanation(
        self, relation_id: int, version_id: int | None = None
    ) -> dict | None:
        if version_id is not None:
            version = self.session.get(GraphVersion, version_id)
            if version is None:
                return None
            snapshot = GraphSnapshotCompatibilityMapper.to_current(version.snapshot)
            relation = next(
                (
                    item for item in snapshot.get("skill_relations", [])
                    if item.get("relation_id") == relation_id
                ),
                None,
            )
            if relation is None:
                return None
            return {
                **dict(relation.get("explanation") or {}),
                "relation_id": relation_id,
                "position_id": version.position_id,
                "skill_id": relation["skill_id"],
                "statistics": _relation_statistics(relation),
                "sources": list(
                    (relation.get("explanation") or {}).get("sources", [])
                ),
                "evidence": list(
                    (relation.get("explanation") or {}).get("evidence", [])
                ),
                "weight_basis": dict(
                    (relation.get("explanation") or {}).get(
                        "weight_basis", {}
                    )
                ),
                "confidence_basis": dict(
                    (relation.get("explanation") or {}).get(
                        "confidence_basis", {}
                    )
                ),
                "quality_impact": dict(
                    (relation.get("explanation") or {}).get(
                        "quality_impact", {}
                    )
                ),
                "manual_modification_history": list(
                    (relation.get("explanation") or {}).get(
                        "manual_modification_history", []
                    )
                ),
                "version_id": version.id,
                "is_current": (
                    self.current_version(version.position_id) is not None
                    and self.current_version(version.position_id).id == version.id
                ),
            }
        current_relation = self.session.get(PositionSkillRelationDraft, relation_id)
        if current_relation is None:
            return None
        current_version = self.current_version(current_relation.position_id)
        if current_version is not None:
            snapshot = GraphSnapshotCompatibilityMapper.to_current(
                current_version.snapshot
            )
            published = next(
                (
                    item for item in snapshot.get("skill_relations", [])
                    if item.get("relation_id") == relation_id
                ),
                None,
            )
            if published is not None:
                return {
                    **dict(published.get("explanation") or {}),
                    "relation_id": relation_id,
                    "position_id": current_relation.position_id,
                    "skill_id": current_relation.skill_id,
                    "statistics": _relation_statistics(published),
                    "sources": list(
                        (published.get("explanation") or {}).get("sources", [])
                    ),
                    "evidence": list(
                        (published.get("explanation") or {}).get("evidence", [])
                    ),
                    "weight_basis": dict(
                        (published.get("explanation") or {}).get(
                            "weight_basis", {}
                        )
                    ),
                    "confidence_basis": dict(
                        (published.get("explanation") or {}).get(
                            "confidence_basis", {}
                        )
                    ),
                    "quality_impact": dict(
                        (published.get("explanation") or {}).get(
                            "quality_impact", {}
                        )
                    ),
                    "manual_modification_history": list(
                        (published.get("explanation") or {}).get(
                            "manual_modification_history", []
                        )
                    ),
                    "version_id": current_version.id,
                    "is_current": True,
                }
        return {
            **build_relation_explanation(self.session, current_relation),
            "version_id": None,
            "is_current": False,
        }

    def visualization(self, position_id: str) -> dict:
        snapshot = self.graph(position_id)
        position = snapshot.get("position", {})
        nodes = [
            {
                "data": {
                    "id": position_id,
                    "label": position.get("name", position_id),
                    "type": "position",
                }
            }
        ]
        categories: dict[str, dict] = {}
        edges = []
        for relation in snapshot.get("skill_relations", []):
            code = relation.get("category_code") or "UNCATEGORIZED"
            category_id = f"category:{code}"
            categories[category_id] = {
                "data": {
                    "id": category_id,
                    "label": relation.get("category_name") or ("未分类" if code == "UNCATEGORIZED" else code),
                    "type": "category",
                    "category_code": code,
                }
            }
            modality = relation.get("primary_modality", "unknown")
            nodes.append(
                {
                    "data": {
                        "id": relation["skill_id"],
                        "label": relation.get("canonical_name", relation["skill_id"]),
                        "type": "skill",
                        "category_code": code,
                        "parent": category_id,
                    }
                }
            )
            edges.append(
                {
                    "data": {
                        "id": f'{position_id}-{relation["skill_id"]}',
                        "source": position_id,
                        "target": relation["skill_id"],
                        "modality": modality,
                        "line_style": "dashed" if modality == "bonus" else "solid",
                        **relation,
                    }
                }
            )
        nodes.extend(categories.values())
        return {"nodes": nodes, "edges": edges}
