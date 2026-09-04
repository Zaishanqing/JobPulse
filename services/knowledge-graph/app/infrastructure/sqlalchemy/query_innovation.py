from __future__ import annotations

from sqlalchemy import select

from app.infrastructure.sqlalchemy.query_base import QuerySession
from app.models import (
    BuildInputWatermarkRecord,
    DependencyAnalysisRunRecord,
    DependencyCandidateRecord,
    DependencyReviewDecisionRecord,
    MappingCandidateRecord,
    MappingReviewDecisionRecord,
    ProjectionManifestRecord,
    RelationClaimRecord,
    DependencyEvent,
)
from app.infrastructure.sqlalchemy.innovation_repository import (
    SqlAlchemyInnovationRepository,
)


class InnovationQueryMixin(QuerySession):
    def change_impact(self, entity_type: str, entity_id: str) -> dict | None:
        if entity_type != "skill_mapping":
            return None
        return SqlAlchemyInnovationRepository(self.session).mapping_change_impact(entity_id)

    def dependency_events(self, entity_type: str, entity_id: str) -> list[dict]:
        rows = self.session.scalars(
            select(DependencyEvent)
            .where(
                DependencyEvent.entity_type == entity_type,
                DependencyEvent.entity_id == entity_id,
            )
            .order_by(DependencyEvent.id)
        ).all()
        return [
            {
                "event_id": row.id,
                "event_key": row.event_key,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "change_kind": row.change_kind,
                "before": row.before_snapshot,
                "after": row.after_snapshot,
                "impact": row.impact_snapshot,
                "actor_id": row.actor_id,
                "trace_id": row.trace_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    def build_watermark(self, build_run_id: int) -> dict | None:
        row = self.session.scalar(
            select(BuildInputWatermarkRecord).where(
                BuildInputWatermarkRecord.build_run_id == build_run_id
            )
        )
        if row is None:
            return None
        return {
            "build_run_id": row.build_run_id,
            "lineage_version": row.lineage_version,
            "source_facts": row.source_facts,
            "observation_window": {
                "start": row.observation_window_start,
                "end": row.observation_window_end,
            },
            "catalog_snapshot_id": row.catalog_snapshot_id,
            "catalog_source_version": row.catalog_source_version,
            "validation_state": row.validation_state,
            "validation_policy_version": row.validation_policy_version,
            "mapping_policy_version": row.mapping_policy_version,
            "aggregation_algorithm_version": row.aggregation_algorithm_version,
            "normalized_config": row.normalized_config,
            "config_version": row.config_version,
            "input_coverage": row.input_coverage,
        }

    def relation_claims(self, graph_version_id: int) -> list[dict]:
        rows = self.session.scalars(
            select(RelationClaimRecord)
            .where(RelationClaimRecord.graph_version_id == graph_version_id)
            .order_by(RelationClaimRecord.claim_id)
        ).all()
        return [
            {
                "claim_id": row.claim_id,
                "support_id": row.support_id,
                "subject_id": row.subject_id,
                "predicate": row.predicate,
                "object_id": row.object_id,
                "claim_kind": row.claim_kind,
                "source_kind": row.source_kind,
                "source_fact_id": row.source_fact_id,
                "source_fact_version": row.source_fact_version,
                "requirement_id": row.requirement_id,
                "evidence": row.evidence_refs,
                "validation_lineage_lineage_version": row.validation_lineage_lineage_version,
                "catalog_snapshot_lineage_version": row.catalog_snapshot_lineage_version,
                "mapping_policy_version": row.mapping_policy_version,
                "observed_at": row.observed_at,
                "lineage_version": row.lineage_version,
            }
            for row in rows
        ]

    def mapping_candidates(self, status: str | None = None) -> list[dict]:
        statement = select(MappingCandidateRecord)
        if status is not None:
            statement = statement.where(MappingCandidateRecord.status == status)
        rows = self.session.scalars(
            statement.order_by(
                MappingCandidateRecord.priority.desc(),
                MappingCandidateRecord.candidate_id,
            )
        ).all()
        candidate_ids = [row.candidate_id for row in rows]
        decisions = (
            self.session.scalars(
                select(MappingReviewDecisionRecord)
                .where(MappingReviewDecisionRecord.candidate_id.in_(candidate_ids))
                .order_by(MappingReviewDecisionRecord.id)
            ).all()
            if candidate_ids
            else []
        )
        decisions_by_candidate: dict[str, list[dict]] = {}
        for decision in decisions:
            decisions_by_candidate.setdefault(decision.candidate_id, []).append(
                {
                    "candidate_revision": decision.candidate_revision,
                    "decision": decision.decision,
                    "reviewer_id": decision.reviewer_id,
                    "reason": decision.reason,
                    "policy_version": decision.policy_version,
                    "decided_at": decision.decided_at,
                    "effective_scope": decision.effective_scope,
                    "replacement_candidate_id": decision.replacement_candidate_id,
                }
            )
        return [
            {
                "candidate_id": row.candidate_id,
                "source_expression": row.source_expression,
                "proposed_skill_id": row.proposed_skill_id,
                "signals": row.signals,
                "priority": row.priority,
                "model_version": row.model_version,
                "index_version": row.index_version,
                "mapping_policy_version": row.mapping_policy_version,
                "affected_contexts": row.affected_contexts,
                "status": row.status,
                "revision": row.revision,
                "decisions": decisions_by_candidate.get(row.candidate_id, []),
            }
            for row in rows
        ]

    def dependency_analysis(self, build_run_id: int) -> dict | None:
        run = self.session.scalar(
            select(DependencyAnalysisRunRecord)
            .where(DependencyAnalysisRunRecord.build_run_id == build_run_id)
            .order_by(DependencyAnalysisRunRecord.id.desc())
        )
        if run is None:
            return None
        candidates = self.session.scalars(
            select(DependencyCandidateRecord)
            .where(DependencyCandidateRecord.analysis_run_id == run.id)
            .order_by(
                DependencyCandidateRecord.prerequisite_skill_id,
                DependencyCandidateRecord.advanced_skill_id,
            )
        ).all()
        reviews = {
            row.dependency_candidate_id: row
            for row in self.session.scalars(
                select(DependencyReviewDecisionRecord).where(
                    DependencyReviewDecisionRecord.dependency_candidate_id.in_(
                        [candidate.id for candidate in candidates]
                    )
                )
            ).all()
        } if candidates else {}
        return {
            "analysis_run_id": run.id,
            "build_run_id": run.build_run_id,
            "policy_hash": run.policy_hash,
            "policy": run.policy,
            "status": run.status,
            "summary": run.summary,
            "candidates": [
                {
                    "candidate_id": row.id,
                    "prerequisite_skill_id": row.prerequisite_skill_id,
                    "advanced_skill_id": row.advanced_skill_id,
                    **row.metrics,
                    "evidence_ids": row.evidence_ids,
                    "claim_kind": row.claim_kind,
                    "review": (
                        {
                            "decision": reviews[row.id].decision,
                            "reviewer_id": reviews[row.id].reviewer_id,
                            "reason": reviews[row.id].reason,
                            "policy_version": reviews[row.id].policy_version,
                            "decided_at": reviews[row.id].decided_at,
                        }
                        if row.id in reviews
                        else None
                    ),
                }
                for row in candidates
            ],
        }

    def graph_projection(
        self, graph_version_id: int, projection_version: str | None = None
    ) -> dict | None:
        statement = select(ProjectionManifestRecord).where(
            ProjectionManifestRecord.graph_version_id == graph_version_id
        )
        if projection_version is not None:
            statement = statement.where(
                ProjectionManifestRecord.projection_version == projection_version
            )
        row = self.session.scalar(statement.order_by(ProjectionManifestRecord.id.desc()))
        if row is None:
            return None
        return {
            "manifest_id": row.id,
            "projection_version": row.projection_version,
            "graph_version_id": row.graph_version_id,
            "watermark_lineage_version": row.watermark_lineage_version,
            "node_count": row.node_count,
            "edge_count": row.edge_count,
            "source_version": row.source_version,
            **row.payload,
        }
