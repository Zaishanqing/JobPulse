from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from app.infrastructure.outbox import SqlAlchemyOutboxRepository

from collections.abc import Mapping

from app.domain.positions import PositionCatalogConflict, PositionSkill
from app.models.jd_publication import JDPublication
from app.models.standard_position import StandardPosition
from app.contexts.catalog import (
    PositionChanges,
    PositionDraft,
    PositionRecord,
)


def _skill_data(skill: PositionSkill) -> dict[str, object]:
    data: dict[str, object] = {
        "skill_id": skill.skill_id,
        "skill_name": skill.skill_name,
        "category": skill.category,
        "weight": skill.weight,
        "confidence": skill.confidence,
        "importance_level": skill.importance_level,
        "trend_score": skill.trend_score,
        "evidence_count": skill.evidence_count,
    }
    if skill.created_at:
        data["created_at"] = skill.created_at
    return data


def _skill(raw: Mapping[str, object], default_level: str = "") -> PositionSkill:
    name = str(raw.get("skill_name") or raw.get("raw_skill") or "unknown")
    skill_id = str(raw.get("skill_id") or raw.get("normalized_skill_id") or "skill_" + name.lower().replace(" ", "_"))
    return PositionSkill(
        skill_id,
        name,
        str(raw.get("category", "未分类")),
        float(raw.get("weight", 0.1)),
        float(raw.get("confidence", 0.9)),
        str(raw.get("importance_level") or default_level),
        float(raw.get("trend_score", 0.0)),
        int(raw.get("evidence_count", 0)),
        str(raw["created_at"]) if raw.get("created_at") else None,
    )


class SqlAlchemyPositionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: PositionDraft) -> PositionRecord:
        row = StandardPosition(
            position_code=draft.position_code,
            position_name=draft.position_name,
            taxonomy_family_code=draft.taxonomy_family_code,
            taxonomy_family_name=draft.taxonomy_family_name,
            skill_domain_codes=list(draft.skill_domain_codes),
            source_emerging_position_id=draft.source_emerging_position_id,
            core_responsibilities=list(draft.core_responsibilities),
            required_skills=[_skill_data(item) for item in draft.required_skills],
            bonus_skills=[_skill_data(item) for item in draft.bonus_skills],
            industry_scenarios=list(draft.industry_scenarios),
            status=draft.status,
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise PositionCatalogConflict("Standard position already exists") from exc
        return self._position(row)

    def get(self, position_id: str) -> PositionRecord | None:
        row = self._session.get(StandardPosition, position_id)
        return self._position(row) if row is not None else None

    def list_positions(self) -> list[PositionRecord]:
        rows = self._session.query(StandardPosition).order_by(StandardPosition.created_at.desc()).all()
        return [self._position(row) for row in rows]

    def jd_counts_by_position(self) -> dict[str, int]:
        rows = (
            self._session.query(
                func.json_extract_path_text(
                    JDPublication.snapshot_payload,
                    "normalized_result",
                    "job_classification",
                    "position_id",
                ).label("position_id"),
                func.count().label("count"),
            )
            .group_by("position_id")
            .all()
        )
        return {
            str(row.position_id): int(row.count)
            for row in rows
            if row.position_id
        }

    def update(self, position_id: str, changes: PositionChanges) -> PositionRecord:
        row = self._session.get(StandardPosition, position_id)
        if row is None:
            raise LookupError(position_id)
        sequence_fields = {"core_responsibilities", "industry_scenarios", "skill_domain_codes"}
        for name in changes.changed_fields:
            value = getattr(changes, name)
            if name in {"required_skills", "bonus_skills"} and value is not None:
                value = [_skill_data(item) for item in value]
            elif name in sequence_fields and value is not None:
                value = list(value)
            setattr(row, name, value)
        self._session.flush()
        return self._position(row)

    def delete(self, position_id: str) -> None:
        row = self._session.get(StandardPosition, position_id)
        if row is None:
            raise LookupError(position_id)
        self._session.delete(row)

    @staticmethod
    def _position(row: StandardPosition) -> PositionRecord:
        return PositionRecord(
            row.id, row.position_name, row.source_emerging_position_id,
            tuple(row.core_responsibilities or []), tuple(_skill(item, "core") for item in row.required_skills or []),
            tuple(_skill(item, "bonus") for item in row.bonus_skills or []), tuple(row.industry_scenarios or []),
            row.status, row.created_at, row.updated_at, row.graph_onboarding_status,
            row.taxonomy_family_code, row.taxonomy_family_name,
            row.position_code, tuple(row.skill_domain_codes or []),
            row.definition,
            tuple(row.aliases or []),
            tuple(row.include_when or []),
            tuple(row.exclude_when or []),
            tuple(row.confusable_with or []),
            row.taxonomy_version,
            row.lifecycle_status,
            row.deprecated_at,
            row.replaced_by,
            row.sample_support_status,
        )


class SqlAlchemyPositionUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyPositionUnitOfWork":
        self._session = self._session_factory()
        self.positions = SqlAlchemyPositionRepository(self._session)
        return self

    def add_outbox(self, draft) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        SqlAlchemyOutboxRepository(self._session).add(draft)

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
