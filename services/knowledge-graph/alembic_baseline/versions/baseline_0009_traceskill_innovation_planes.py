"""baseline equivalent of the TraceSkill innovation persistence planes

Revision ID: baseline_0009_traceskill_innovation_planes
Revises: baseline_0008_published_fact_lineage
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


revision = "baseline_0009_traceskill_innovation_planes"
down_revision = "baseline_0008"
branch_labels = None
depends_on = None

_source = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0011_traceskill_innovation_planes.py"
)
_spec = spec_from_file_location("traceskill_innovation_migration", _source)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load TraceSkill innovation migration")
_migration = module_from_spec(_spec)
_spec.loader.exec_module(_migration)


def upgrade():
    _migration.upgrade()


def downgrade():
    raise RuntimeError("Baseline TraceSkill migration is forward-only")


