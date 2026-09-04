import re

from sqlalchemy import select

from app.application.mappers import GraphSnapshotCompatibilityMapper
from app.domain.evolution import detect_evolution_events
from app.infrastructure.sqlalchemy.query_base import QuerySession, compact_graph_snapshot
from app.models import (
    ExtractionEvidence,
    AuditLog,
    GraphBuildRun,
    GraphVersion,
    PositionSkillSupport,
    StandardPosition,
)


def _comparable(value: dict) -> dict:
    result = {key: item for key, item in value.items() if key != "relation_id"}
    if result.get("trend_score") in (None, 0, 0.0):
        result["trend_score"] = None
    return result


def _changed_fields(before: dict, after: dict) -> dict:
    left, right = _comparable(before), _comparable(after)
    return {
        key: {"before": left.get(key), "after": right.get(key)}
        for key in left.keys() | right.keys()
        if left.get(key) != right.get(key)
    }


def _business_relation_changes(before: dict, after: dict) -> dict:
    def values(fields: tuple[str, ...]) -> dict:
        return {
            field: {"before": before.get(field), "after": after.get(field)}
            for field in fields
            if before.get(field) != after.get(field)
        }

    result = {
        "weight": values(("auto_weight", "manual_weight", "final_weight", "weight")),
        "confidence": values((
            "auto_confidence", "manual_confidence", "final_confidence", "confidence",
        )),
        "importance_level": values((
            "auto_importance_level", "manual_importance_level",
            "final_importance_level", "importance_level",
        )),
    }
    before_statistics = before.get("statistics") or {}
    after_statistics = after.get("statistics") or {}
    result["support_data"] = {
        field: {
            "before": before_statistics.get(field),
            "after": after_statistics.get(field),
        }
        for field in before_statistics.keys() | after_statistics.keys()
        if before_statistics.get(field) != after_statistics.get(field)
    }
    return {key: value for key, value in result.items() if value}


def _change_sources(changes: dict) -> list[str]:
    sources = []
    if "support_data" in changes:
        sources.append("support_data")
    if any(
        field.startswith("manual_")
        for group in ("weight", "confidence", "importance_level")
        for field in changes.get(group, {})
    ):
        sources.append("manual_modification")
    if any(group in changes for group in ("weight", "confidence", "importance_level")):
        sources.append("calculation")
    return sources


def _business_value(value):
    if isinstance(value, list):
        return [_business_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _business_value(item)
            for key, item in value.items()
            if key not in {"aggregate_id", "relation_id"}
        }
    return value


def _context_value(field: str, value):
    normalized = _business_value(value)
    if field == "sample_stats" and isinstance(normalized, dict):
        # Manual edits are relation changes with their own history/source.
        # They must not also be reported as a graph-context change.
        normalized.pop("manual_modifications", None)
    return normalized


def _capability_relation(value: dict) -> dict:
    metrics = value.get("metrics") or {}
    statistics = value.get("statistics") or {}
    return {
        key: value.get(key)
        for key in (
            "skill_id", "canonical_name", "category_code", "category_name",
            "weight", "confidence", "importance_level", "trend_score",
        )
    } | {
        "metrics": {
            "support_document_count": metrics.get("support_document_count", 0),
        },
        "statistics": {
            "evidence_count": statistics.get("evidence_count", 0),
        },
    }


def _capability_snapshot(value: dict) -> dict:
    return {
        "position_id": value.get("position_id"),
        "position": value.get("position"),
        "skill_relations": [
            _capability_relation(item)
            for item in value.get("skill_relations", [])
        ],
    }


def _capability_comparison(value: dict) -> dict:
    return {
        "from_version_id": value["from_version_id"],
        "to_version_id": value["to_version_id"],
        "added": [
            _capability_relation(item) for item in value.get("added", [])
        ],
        "removed": [
            _capability_relation(item) for item in value.get("removed", [])
        ],
        "changed": [
            {
                "skill_id": item.get("skill_id"),
                "changed_fields": sorted(item.get("changed_fields", {})),
                "change_sources": item.get("change_sources", []),
            }
            for item in value.get("changed", [])
        ],
        "summary": value.get("summary", {}),
        "context_change_fields": sorted(value.get("context_changes", {})),
    }


class VersionQueryMixin(QuerySession):
    def _version_metadata(self, row: GraphVersion) -> dict:
        source = (
            self.session.get(GraphVersion, row.rollback_from_version_id)
            if row.rollback_from_version_id is not None
            else None
        )
        rollback_audit = self.session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "rollback_graph",
                AuditLog.object_type == "graph_version",
                AuditLog.object_id == str(row.id),
            )
            .order_by(AuditLog.id.desc())
        )
        position = self.session.scalar(
            select(StandardPosition).where(
                StandardPosition.position_id == row.position_id
            )
        )
        return {
            "rollback_from_version_number": source.version_number if source else None,
            "rollback_reason": rollback_audit.reason if rollback_audit else None,
            "is_current": bool(position and position.current_version_id == row.id),
        }

    def versions(self, position_id: str) -> list[dict]:
        rows = self.session.scalars(
            select(GraphVersion)
            .where(GraphVersion.position_id == position_id)
            .order_by(GraphVersion.version_number)
        ).all()
        return [
            {
                "id": row.id,
                "version_number": row.version_number,
                "version_name": row.version_name,
                "build_run_id": row.build_run_id,
                "release_id": row.release_id,
                "rollback_from_version_id": row.rollback_from_version_id,
                "dependencies": {
                    "published_fact_versions": list(row.published_fact_versions),
                    "skill_catalog_version": row.skill_catalog_version,
                    "mapping_snapshot_version": row.mapping_snapshot_version,
                    "normalization_algorithm_version": row.normalization_algorithm_version,
                    "build_config_version": row.build_config_version,
                    "source_time_window": dict(row.source_time_window),
                },
                **self._version_metadata(row),
                "created_at": row.published_at,
            }
            for row in rows
        ]

    def version(self, position_id: str, version_id: int) -> dict | None:
        row = self.session.get(GraphVersion, version_id)
        if row is None or row.position_id != position_id:
            return None
        run = self.session.get(GraphBuildRun, row.build_run_id) if row.build_run_id else None
        return {
            "version_id": row.id,
            "version_number": row.version_number,
            "position_id": row.position_id,
            "build_run_id": row.build_run_id,
            "release_id": row.release_id,
            "base_version_id": run.base_version_id if run else row.snapshot.get("base_version_id"),
            "snapshot": compact_graph_snapshot(row.snapshot),
            "source_version": row.source_version,
            "created_at": row.created_at,
            "published_by": row.published_by,
            "rollback_from_version_id": row.rollback_from_version_id,
            "dependencies": {
                "published_fact_versions": list(row.published_fact_versions),
                "skill_catalog_version": row.skill_catalog_version,
                "mapping_snapshot_version": row.mapping_snapshot_version,
                "normalization_algorithm_version": row.normalization_algorithm_version,
                "build_config_version": row.build_config_version,
                "source_time_window": dict(row.source_time_window),
            },
            **self._version_metadata(row),
        }

    def version_diff(
        self,
        position_id: str,
        before_id: int,
        after_id: int,
        *,
        include_evidence_changes: bool = True,
    ) -> dict | None:
        before = self.session.get(GraphVersion, before_id)
        after = self.session.get(GraphVersion, after_id)
        if (
            before is None
            or after is None
            or before.position_id != position_id
            or after.position_id != position_id
        ):
            return None
        before_snapshot = GraphSnapshotCompatibilityMapper.to_current(before.snapshot)
        after_snapshot = GraphSnapshotCompatibilityMapper.to_current(after.snapshot)
        left = {item["skill_id"]: item for item in before_snapshot.get("skill_relations", [])}
        right = {item["skill_id"]: item for item in after_snapshot.get("skill_relations", [])}
        common = left.keys() & right.keys()
        added = [
            {**right[key], "change_sources": ["support_data"]}
            for key in sorted(right.keys() - left.keys())
        ]
        removed = [
            {**left[key], "change_sources": ["support_data"]}
            for key in sorted(left.keys() - right.keys())
        ]
        changed = [
            {
                "skill_id": key,
                "before": left[key],
                "after": right[key],
                "changed_fields": _changed_fields(left[key], right[key]),
                "business_changes": _business_relation_changes(
                    left[key], right[key]
                ),
            }
            for key in sorted(common)
            if _comparable(left[key]) != _comparable(right[key])
        ]
        for item in changed:
            item["change_sources"] = _change_sources(item["business_changes"])
        result = {
            "from_version_id": before.id,
            "to_version_id": after.id,
            "added": added,
            "removed": removed,
            "changed": changed,
        }
        context_changes = {}
        for field in (
            "responsibilities",
            "requirement_profile",
            "company_context",
            "employment_context",
            "sample_stats",
            "algorithm_metadata",
            "normalization_metadata",
        ):
            if _context_value(field, before_snapshot.get(field)) != _context_value(
                field, after_snapshot.get(field)
            ):
                context_changes[field] = {
                    "before": before_snapshot.get(field),
                    "after": after_snapshot.get(field),
                }
        result["context_changes"] = context_changes
        def evidence_by_skill(build_run_id: int) -> dict[str, list[dict]]:
            supports = self.session.scalars(
                select(PositionSkillSupport).where(
                    PositionSkillSupport.build_run_id == build_run_id
                )
            ).all()
            evidence_ids = {
                support.evidence_id
                for support in supports
                if support.evidence_id is not None
            }
            evidence_by_id = {
                evidence.id: evidence
                for evidence in self.session.scalars(
                    select(ExtractionEvidence).where(
                        ExtractionEvidence.id.in_(evidence_ids)
                    )
                ).all()
            } if evidence_ids else {}
            grouped: dict[str, list[dict]] = {}
            for support in supports:
                evidence = evidence_by_id.get(support.evidence_id)
                grouped.setdefault(support.skill_id, []).append({
                    "document_id": support.document_id,
                    "requirement_id": support.requirement_id,
                    "evidence_id": support.evidence_id,
                    "quote": evidence.quote if evidence is not None else None,
                    "alignment": evidence.alignment if evidence is not None else None,
                })
            return grouped
        if include_evidence_changes:
            before_evidence = evidence_by_skill(before.build_run_id)
            after_evidence = evidence_by_skill(after.build_run_id)
            result["evidence_changes"] = [
                {
                    "skill_id": skill_id,
                    "before": before_evidence.get(skill_id, []),
                    "after": after_evidence.get(skill_id, []),
                }
                for skill_id in sorted(before_evidence.keys() | after_evidence.keys())
                if before_evidence.get(skill_id, []) != after_evidence.get(skill_id, [])
            ]
            evidence_skill_ids = {
                item["skill_id"] for item in result["evidence_changes"]
            }
        else:
            result["evidence_changes"] = []
            evidence_skill_ids = {
                item["skill_id"]
                for item in result["changed"]
                if "support_data" in item["business_changes"]
            }
        for item in result["changed"]:
            if (
                item["skill_id"] in evidence_skill_ids
                and "support_data" not in item["change_sources"]
            ):
                item["change_sources"].append("support_data")
        rollback_source = None
        if after.rollback_from_version_id is not None:
            rollback_source = {
                "source_version_id": after.rollback_from_version_id,
                **self._version_metadata(after),
            }
            for item in result["added"] + result["removed"] + result["changed"]:
                if "rollback" not in item["change_sources"]:
                    item["change_sources"].append("rollback")
        result["rollback_source"] = rollback_source
        result["summary"] = {
            "added": len(result["added"]),
            "removed": len(result["removed"]),
            "changed": len(result["changed"]),
            "support_changed": len(evidence_skill_ids),
            "context_changed": len(context_changes),
        }
        return result

    def _version_pair(self, position_id: str, before_id: int, after_id: int):
        before = self.session.get(GraphVersion, before_id)
        after = self.session.get(GraphVersion, after_id)
        if (
            before is None
            or after is None
            or before.position_id != position_id
            or after.position_id != position_id
        ):
            return None, None
        return before, after

    def evolution_events(
        self,
        position_id: str,
        before_id: int,
        after_id: int,
        event_type: str | None = None,
    ) -> dict | None:
        before, after = self._version_pair(position_id, before_id, after_id)
        if before is None or after is None:
            return None
        events = detect_evolution_events(
            before.snapshot,
            after.snapshot,
            position_id=position_id,
            from_version_id=before_id,
            to_version_id=after_id,
            created_at=after.published_at,
        )
        if event_type:
            events = [item for item in events if item["event_type"] == event_type]
        return {
            "position_id": position_id,
            "from_version_id": before_id,
            "to_version_id": after_id,
            "event_type": event_type,
            "events": events,
            "count": len(events),
        }

    def evolution_event(self, position_id: str, event_id: str) -> dict | None:
        match = re.fullmatch(
            r"evt-(\d+)-(\d+)-([a-z_]+)-(\d+)",
            event_id,
        )
        if match is None:
            return None
        before_id = int(match.group(1))
        after_id = int(match.group(2))
        result = self.evolution_events(position_id, before_id, after_id)
        if result is None:
            return None
        for event in result["events"]:
            if event["event_id"] == event_id:
                return event
        return None

    def capability_evolution(self, position_id: str) -> dict:
        """Return the complete published graph-version timeline for one position.

        This contract deliberately contains no market/trend-intelligence data.  Every
        frame is an immutable GraphVersion snapshot and every change is calculated
        from two adjacent published snapshots.
        """
        versions = self.versions(position_id)
        bounded_versions = [
            item
            for item in versions
            if (item.get("dependencies") or {}).get("source_time_window", {}).get("start")
            and (item.get("dependencies") or {}).get("source_time_window", {}).get("end")
        ]
        if len(bounded_versions) >= 2:
            versions = bounded_versions
        versions.sort(key=lambda item: (
            str(
                (item.get("dependencies") or {}).get("source_time_window", {}).get("end")
                or item.get("created_at")
                or ""
            ),
            int(item["version_number"]),
        ))
        frames: list[dict] = []
        comparisons: list[dict] = []
        events: list[dict] = []
        for item in versions:
            version = self.version(position_id, int(item["id"]))
            if version is None:
                continue
            frames.append({
                **item,
                "snapshot": _capability_snapshot(version["snapshot"]),
            })
        for before, after in zip(frames, frames[1:]):
            before_id = int(before["id"])
            after_id = int(after["id"])
            comparison = self.version_diff(
                position_id,
                before_id,
                after_id,
                include_evidence_changes=False,
            )
            event_collection = self.evolution_events(
                position_id, before_id, after_id
            )
            if comparison is not None:
                comparisons.append(_capability_comparison(comparison))
            if event_collection is not None:
                events.extend(event_collection["events"])
        return {
            "schema_version": "capability-evolution.v1",
            "position_id": position_id,
            "frames": frames,
            "comparisons": comparisons,
            "events": events,
            "frame_count": len(frames),
            "comparison_count": len(comparisons),
            "event_count": len(events),
        }
