"""bind KG standard positions to position-taxonomy.v3 codes

Revision ID: 0025_position_taxonomy_v3
Revises: 0024_widen_watermark_validation_policy_version
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_position_taxonomy_v3"
down_revision = "0024_widen_watermark_validation_policy_version"
branch_labels = None
depends_on = None


POSITION_REFERENCES = (
    ("graph_build_runs", "position_id"),
    ("graph_build_jobs", "position_id"),
    ("graph_versions", "position_id"),
    ("position_skill_relation_drafts", "position_id"),
    ("position_skill_supports", "position_id"),
    ("relation_claims", "subject_id"),
)
NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _restore_sqlite_immutability_triggers() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for table_name in ("graph_versions", "relation_claims"):
        for action in ("update", "delete"):
            trigger_name = f"trg_{table_name}_reject_{action}"
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
            op.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE {action.upper()} "
                f"ON {table_name} "
                f"BEGIN SELECT RAISE(ABORT, '{table_name} is immutable'); END"
            )


def _replace_position_foreign_keys(*, cascade: bool, length: int) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name, column_name in POSITION_REFERENCES:
        constraint = next(
            (
                item
                for item in inspector.get_foreign_keys(table_name)
                if item.get("referred_table") == "standard_positions"
                and item.get("constrained_columns") == [column_name]
            ),
            None,
        )
        if constraint is None:
            raise RuntimeError(f"missing standard position foreign key: {table_name}.{column_name}")
        reflected_name = constraint.get("name") or (
            f"fk_{table_name}_{column_name}_standard_positions"
        )
        target_name = f"fk_{table_name}_{column_name}_position_identity"
        with op.batch_alter_table(
            table_name,
            naming_convention=NAMING_CONVENTION,
        ) as batch:
            batch.drop_constraint(reflected_name, type_="foreignkey")
            batch.alter_column(
                column_name,
                existing_type=sa.String(length=80 if length == 100 else 100),
                type_=sa.String(length=length),
                existing_nullable=False,
            )
            batch.create_foreign_key(
                target_name,
                "standard_positions",
                [column_name],
                ["position_id"],
                onupdate="CASCADE" if cascade else None,
            )
            if table_name == "graph_build_jobs":
                batch.create_check_constraint(
                    "ck_graph_build_jobs_status",
                    "status IN ('queued', 'running', 'succeeded', 'failed')",
                )
                batch.create_check_constraint(
                    "ck_graph_build_jobs_attempts",
                    "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts",
                )


def upgrade() -> None:
    with op.batch_alter_table("standard_positions") as batch:
        batch.alter_column(
            "position_id",
            existing_type=sa.String(length=80),
            type_=sa.String(length=100),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("position_code", sa.String(length=100)))
        batch.add_column(sa.Column("taxonomy_version", sa.String(length=64)))
        batch.add_column(
            sa.Column(
                "sample_support_status",
                sa.String(length=16),
                nullable=False,
                server_default="none",
            )
        )
        batch.create_index(
            "ix_kg_standard_positions_position_code",
            ["position_code"],
            unique=True,
        )
        batch.create_index("ix_kg_standard_positions_taxonomy_version", ["taxonomy_version"])
    _replace_position_foreign_keys(cascade=True, length=100)
    _restore_sqlite_immutability_triggers()


def downgrade() -> None:
    _replace_position_foreign_keys(cascade=False, length=80)
    _restore_sqlite_immutability_triggers()
    with op.batch_alter_table("standard_positions") as batch:
        batch.drop_index("ix_kg_standard_positions_taxonomy_version")
        batch.drop_index("ix_kg_standard_positions_position_code")
        batch.drop_column("sample_support_status")
        batch.drop_column("taxonomy_version")
        batch.drop_column("position_code")
        batch.alter_column(
            "position_id",
            existing_type=sa.String(length=100),
            type_=sa.String(length=80),
            existing_nullable=False,
        )
