from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0009_authoritative_published_facts.py"
)


def test_0009_explicitly_rejects_destructive_downgrade():
    spec = spec_from_file_location("migration_0009", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    with pytest.raises(
        RuntimeError,
        match="Migration 0009 is forward-only and cannot be downgraded",
    ):
        migration.downgrade()
