from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.errors import NotFoundError
from app.domain.review import ReviewObjectHandlerRegistry
from app.domain.review_tasks import ReviewObjectEffect
from app.models import (
    PositionRequirementAggregateDraft,
    PositionSkillRelationDraft,
    PositionTaskAggregateDraft,
)


class NoOpReviewHandler:
    """Objects without a secondary status are fully represented by ReviewTask."""

    def apply(self, object_id: str, build_run_id: int | None, target_status: str) -> dict:
        return {"object_id": object_id, "status": target_status}


class PositionRelationReviewHandler:
    def __init__(self, session: Session):
        self.session = session

    def apply(self, object_id: str, build_run_id: int | None, target_status: str) -> dict:
        relation = self.session.get(
            PositionSkillRelationDraft, int(object_id)
        ) if object_id.isdigit() else self.session.scalar(
            select(PositionSkillRelationDraft).where(
                PositionSkillRelationDraft.build_run_id == build_run_id,
                PositionSkillRelationDraft.skill_id == object_id.split(":")[-1],
            )
        )
        if not relation:
            raise NotFoundError("review relation not found")
        if build_run_id is not None and relation.build_run_id != build_run_id:
            raise NotFoundError("review relation does not belong to build")
        before = {"status": relation.status}
        relation.status = target_status
        return {"before": before, "after": {"status": relation.status}}


class EvidenceReviewHandler(NoOpReviewHandler):
    pass


class SkillNormalizationReviewHandler(NoOpReviewHandler):
    pass


class PositionRequirementReviewHandler:
    def __init__(self, session: Session):
        self.session = session

    def apply(self, object_id: str, build_run_id: int | None, target_status: str) -> dict:
        aggregate = (
            self.session.scalar(
                select(PositionRequirementAggregateDraft).where(
                    PositionRequirementAggregateDraft.id == int(object_id),
                    PositionRequirementAggregateDraft.build_run_id
                    == build_run_id,
                )
            )
            if build_run_id is not None
            else self.session.get(
                PositionRequirementAggregateDraft, int(object_id)
            )
        )
        if not aggregate:
            raise NotFoundError("review requirement not found")
        before = {"status": aggregate.status}
        aggregate.status = target_status
        return {"before": before, "after": {"status": aggregate.status}}


class PositionTaskReviewHandler:
    def __init__(self, session: Session):
        self.session = session

    def apply(self, object_id: str, build_run_id: int | None, target_status: str) -> dict:
        aggregate = (
            self.session.scalar(
                select(PositionTaskAggregateDraft).where(
                    PositionTaskAggregateDraft.id == int(object_id),
                    PositionTaskAggregateDraft.build_run_id == build_run_id,
                )
            )
            if build_run_id is not None
            else self.session.get(PositionTaskAggregateDraft, int(object_id))
        )
        if not aggregate:
            raise NotFoundError("review task aggregate not found")
        before = {"status": aggregate.status}
        aggregate.status = target_status
        return {"before": before, "after": {"status": aggregate.status}}


class GraphVersionReviewHandler(NoOpReviewHandler):
    """Overall review disposition lives on the review task, not the build run."""


def build_review_handler_registry(session: Session) -> ReviewObjectHandlerRegistry:
    default = NoOpReviewHandler()
    return ReviewObjectHandlerRegistry(
        {
            "evidence": EvidenceReviewHandler(),
            "normalization_item": SkillNormalizationReviewHandler(),
            "position_skill_relation": PositionRelationReviewHandler(session),
            "position_requirement": PositionRequirementReviewHandler(session),
            "position_task": PositionTaskReviewHandler(session),
            "graph_version": GraphVersionReviewHandler(),
        },
        default,
    )


class SqlAlchemyReviewObjectEffectAdapter:
    def __init__(self, session: Session):
        self.registry = build_review_handler_registry(session)

    def apply(self, effect: ReviewObjectEffect) -> None:
        self.registry.handler_for(effect.object_type).apply(
            effect.object_id, effect.build_run_id, effect.target_status
        )
