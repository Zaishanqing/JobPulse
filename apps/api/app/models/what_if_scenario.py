from __future__ import annotations

from sqlalchemy import Column, DateTime, JSON, String

from app.core.database import Base
from app.models.user import utc_now


class WhatIfScenario(Base):
    """Immutable snapshot of one counterfactual matching scenario."""

    __tablename__ = "what_if_scenarios"

    scenario_id = Column(String(80), primary_key=True)
    evaluation_id = Column(String(200), nullable=False, index=True)
    actions_payload = Column(JSON, nullable=False)
    result_payload = Column(JSON, nullable=False)
    release_id = Column(String(128), nullable=True, index=True)
    graph_version = Column(String(255), nullable=True)
    algorithm_version = Column(String(255), nullable=True)
    config_version = Column(String(255), nullable=True)
    created_by = Column(String(36), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
