"""baseline equivalent of K0 governance effects"""
from __future__ import annotations
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

revision = "baseline_0011_k0_governance_effects"
down_revision = "baseline_0010_k0_release_lineage"
branch_labels = None
depends_on = None
_source = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0013_k0_governance_effects.py"
_spec = spec_from_file_location("k0_governance_effects_migration", _source)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load K0 governance effects migration")
_migration = module_from_spec(_spec)
_spec.loader.exec_module(_migration)

def upgrade():
    _migration.upgrade()

def downgrade():
    raise RuntimeError("Baseline K0 governance migration is forward-only")


