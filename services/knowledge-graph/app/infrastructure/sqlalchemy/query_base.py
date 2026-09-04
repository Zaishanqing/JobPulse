from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ExtractionEvidence, GraphBuildRun, GraphVersion, StandardPosition


def position_build_version(session: Session, run: GraphBuildRun) -> int:
    """Return the 1-based build version within a single position."""
    return int(
        session.scalar(
            select(func.count(GraphBuildRun.id)).where(
                GraphBuildRun.position_id == run.position_id,
                GraphBuildRun.id <= run.id,
            )
        )
        or 0
    )


def evidence_projection(row: ExtractionEvidence) -> dict:
    return {
        "id": row.id,
        "owner_type": row.owner_type,
        "owner_ref": row.owner_ref,
        "quote": row.quote,
        "start": row.start,
        "end": row.end,
        "alignment": row.alignment,
        "occurrence_index": row.occurrence_index,
    }


class QuerySession:
    def __init__(self, session: Session):
        self.session = session

    def latest(self, model, document_id: str):
        return self.session.scalar(
            select(model).where(model.document_id == document_id).order_by(model.id.desc())
        )

    def current_version(self, position_id: str):
        position = self.session.scalar(
            select(StandardPosition).where(StandardPosition.position_id == position_id)
        )
        if position is None or position.current_version_id is None:
            return None
        return self.session.get(GraphVersion, position.current_version_id)


def compact_graph_snapshot(snapshot):
    """Return the current snapshot contract without heavy relation explanations.

    Relation explanation is served by the dedicated relation-explanation
    endpoint; graph/version list views only need weights, modalities and
    statistics. Dropping the blobs here keeps the BFF freeze/thaw fast.
    """

    from app.application.mappers import GraphSnapshotCompatibilityMapper

    current = GraphSnapshotCompatibilityMapper.to_current(snapshot)
    for relation in current.get("skill_relations", []):
        if isinstance(relation, dict):
            relation.pop("explanation", None)
    current.pop("evidence_summary", None)
    return current
