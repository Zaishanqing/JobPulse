"""make graph editing and publication concurrency-safe

Revision ID: 0008_graph_edit_concurrency
Revises: 0007_resolution_source
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "0008_graph_edit_concurrency"
down_revision = "0007_resolution_source"
branch_labels = None
depends_on = None


def _json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def _columns(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table):
    inspector = sa.inspect(bind)
    result = {item["name"] for item in inspector.get_indexes(table)}
    result.update(item["name"] for item in inspector.get_unique_constraints(table))
    return result


def _foreign_keys(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_foreign_keys(table)}


def _foreign_key_specs(bind, table):
    return sa.inspect(bind).get_foreign_keys(table)


def _drop_graph_version_immutability(bind):
    dialect = bind.dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_graph_versions_reject_update")
        op.execute("DROP TRIGGER IF EXISTS trg_graph_versions_reject_delete")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_graph_versions_reject_update ON graph_versions"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_graph_versions_reject_delete ON graph_versions"
        )


def _restore_graph_version_immutability(bind):
    dialect = bind.dialect.name
    if dialect == "sqlite":
        op.execute("""CREATE TRIGGER trg_graph_versions_reject_update
            BEFORE UPDATE ON graph_versions BEGIN
            SELECT RAISE(ABORT, 'graph_versions are immutable'); END""")
        op.execute("""CREATE TRIGGER trg_graph_versions_reject_delete
            BEFORE DELETE ON graph_versions BEGIN
            SELECT RAISE(ABORT, 'graph_versions are immutable'); END""")
    elif dialect == "postgresql":
        op.execute("""CREATE TRIGGER trg_graph_versions_reject_update
            BEFORE UPDATE ON graph_versions FOR EACH ROW
            EXECUTE FUNCTION reject_graph_version_mutation()""")
        op.execute("""CREATE TRIGGER trg_graph_versions_reject_delete
            BEFORE DELETE ON graph_versions FOR EACH ROW
            EXECUTE FUNCTION reject_graph_version_mutation()""")


def _repair_version_build_relationships(bind):
    """Preserve every legacy version while giving it one dedicated build run."""
    metadata = sa.MetaData()
    builds = sa.Table("graph_build_runs", metadata, autoload_with=bind)
    graph_versions = sa.Table("graph_versions", metadata, autoload_with=bind)
    versions = bind.execute(
        sa.select(
            graph_versions.c.id,
            graph_versions.c.position_id,
            graph_versions.c.build_run_id,
            graph_versions.c.published_at,
        ).order_by(
            graph_versions.c.position_id,
            graph_versions.c.version_number,
            graph_versions.c.id,
        )
    ).mappings().all()
    source_builds = {
        row["id"]: dict(row)
        for row in bind.execute(sa.select(builds)).mappings()
    }
    used_builds = set()
    previous_by_position = {}
    now = datetime.now(timezone.utc)

    for version in versions:
        source_id = version["build_run_id"]
        build_id = source_id
        base_version_id = previous_by_position.get(version["position_id"])
        if source_id is None or source_id in used_builds:
            source = source_builds.get(source_id, {})
            config = _json(source.get("config_snapshot"))
            config["migration_recovered_publication"] = {
                "source_build_run_id": source_id,
                "graph_version_id": version["id"],
            }
            values = {
                "position_id": version["position_id"],
                "base_version_id": base_version_id,
                "active_draft_key": None,
                "status": "published",
                "window_start": source.get("window_start"),
                "window_end": source.get("window_end"),
                "config_snapshot": config,
                "summary": _json(source.get("summary")),
                "created_at": source.get("created_at") or version["published_at"] or now,
            }
            result = bind.execute(builds.insert().values(**values))
            build_id = result.inserted_primary_key[0]
            bind.execute(
                graph_versions.update()
                .where(graph_versions.c.id == version["id"])
                .values(build_run_id=build_id)
            )
        bind.execute(
            builds.update().where(builds.c.id == build_id).values(
                base_version_id=base_version_id,
                active_draft_key=None,
                status="published",
            )
        )
        used_builds.add(build_id)
        previous_by_position[version["position_id"]] = version["id"]


def upgrade():
    bind = op.get_bind()
    columns = _columns(bind, "graph_build_runs")
    if "base_version_id" not in columns:
        op.add_column(
            "graph_build_runs", sa.Column("base_version_id", sa.Integer(), nullable=True)
        )
    if "active_draft_key" not in columns:
        op.add_column(
            "graph_build_runs",
            sa.Column("active_draft_key", sa.String(length=180), nullable=True),
        )
    indexes = _indexes(bind, "graph_build_runs")
    if "uq_graph_build_run_id_position" not in indexes:
        op.create_index(
            "uq_graph_build_run_id_position", "graph_build_runs",
            ["id", "position_id"], unique=True,
        )
    if "uq_graph_build_run_active_draft_key" not in indexes:
        op.create_index(
            "uq_graph_build_run_active_draft_key", "graph_build_runs",
            ["active_draft_key"], unique=True,
        )
    if "ix_graph_build_runs_base_version_id" not in indexes:
        op.create_index(
            "ix_graph_build_runs_base_version_id", "graph_build_runs",
            ["base_version_id"], unique=False,
        )

    rows = bind.execute(sa.text(
        "SELECT id, position_id, status, config_snapshot FROM graph_build_runs"
    )).all()
    draft_candidates = {}
    for run_id, position_id, status, config in rows:
        config_value = _json(config)
        base_version_id = config_value.get("base_version_id")
        if base_version_id is not None:
            bind.execute(sa.text(
                "UPDATE graph_build_runs SET base_version_id=:base WHERE id=:run"
            ), {"base": base_version_id, "run": run_id})
        if base_version_id is not None and config_value.get("draft_source"):
            draft_candidates.setdefault((position_id, base_version_id), []).append(run_id)

    _drop_graph_version_immutability(bind)
    _repair_version_build_relationships(bind)
    versions = bind.execute(sa.text(
        "SELECT id, position_id, build_run_id FROM graph_versions "
        "ORDER BY position_id, version_number"
    )).all()
    published_runs = {item[2] for item in versions}
    for (position_id, base_version_id), run_ids in draft_candidates.items():
        unpublished = sorted((item for item in run_ids if item not in published_runs), reverse=True)
        if not unpublished:
            continue
        bind.execute(sa.text(
            "UPDATE graph_build_runs SET active_draft_key=:key WHERE id=:run"
        ), {"key": f"{position_id}:{base_version_id}", "run": unpublished[0]})
        for duplicate in unpublished[1:]:
            bind.execute(sa.text(
                "UPDATE graph_build_runs SET status='cancelled' WHERE id=:run"
            ), {"run": duplicate})

    if "revision" not in _columns(bind, "position_skill_relation_drafts"):
        op.add_column(
            "position_skill_relation_drafts",
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        )
    relation_foreign_keys = _foreign_key_specs(
        bind, "position_skill_relation_drafts"
    )
    has_composite_build_position = any(
        item["constrained_columns"] == ["build_run_id", "position_id"]
        for item in relation_foreign_keys
    )
    standalone_build = next((
        item for item in relation_foreign_keys
        if item["constrained_columns"] == ["build_run_id"]
        and item["referred_table"] == "graph_build_runs"
    ), None)
    naming_convention = {
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    }
    with op.batch_alter_table(
        "position_skill_relation_drafts",
        naming_convention=naming_convention,
    ) as batch:
        if standalone_build is not None:
            batch.drop_constraint(
                standalone_build["name"]
                or "fk_position_skill_relation_drafts_build_run_id_graph_build_runs",
                type_="foreignkey",
            )
        if not has_composite_build_position:
            batch.create_foreign_key(
                "fk_relation_draft_build_position", "graph_build_runs",
                ["build_run_id", "position_id"], ["id", "position_id"],
            )
        batch.alter_column(
            "status", existing_type=sa.String(length=30), server_default=None
        )

    if "uq_graph_version_build_run" not in _indexes(bind, "graph_versions"):
        op.create_index(
            "uq_graph_version_build_run", "graph_versions", ["build_run_id"], unique=True
        )
    with op.batch_alter_table("graph_versions") as batch:
        batch.alter_column(
            "build_run_id", existing_type=sa.Integer(), nullable=False
        )
    _restore_graph_version_immutability(bind)


def downgrade():
    bind = op.get_bind()
    _drop_graph_version_immutability(bind)
    with op.batch_alter_table("graph_versions") as batch:
        batch.alter_column(
            "build_run_id", existing_type=sa.Integer(), nullable=True
        )
    _restore_graph_version_immutability(bind)
    op.drop_index("uq_graph_version_build_run", table_name="graph_versions")

    with op.batch_alter_table("position_skill_relation_drafts") as batch:
        batch.drop_constraint("fk_relation_draft_build_position", type_="foreignkey")
        batch.create_foreign_key(
            "fk_relation_draft_build", "graph_build_runs", ["build_run_id"], ["id"]
        )
        batch.alter_column(
            "status", existing_type=sa.String(length=30), server_default="in_review"
        )
    op.drop_column("position_skill_relation_drafts", "revision")

    op.drop_index("ix_graph_build_runs_base_version_id", table_name="graph_build_runs")
    op.drop_index("uq_graph_build_run_active_draft_key", table_name="graph_build_runs")
    op.drop_index("uq_graph_build_run_id_position", table_name="graph_build_runs")
    op.drop_column("graph_build_runs", "active_draft_key")
    op.drop_column("graph_build_runs", "base_version_id")
