"""extend the stable baseline with immutable published-fact lineage"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "baseline_0008"
down_revision = "baseline_0007"
branch_labels = None
depends_on = None

UPDATE_TRIGGER = "trg_published_fact_lineages_reject_update"
DELETE_TRIGGER = "trg_published_fact_lineages_reject_delete"
POSTGRES_FUNCTION = "reject_published_fact_lineage_mutation"


def upgrade():
    op.create_table(
        "published_fact_lineages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_fact_import_id", sa.Integer(), nullable=False),
        sa.Column("lineage_fingerprint", sa.String(64), nullable=False),
        sa.Column("data_validation_task_id", sa.String(100)),
        sa.Column("validation_report_id", sa.String(100)),
        sa.Column("validated_bundle_snapshot_id", sa.String(100)),
        sa.Column("validation_policy_version", sa.String(80)),
        sa.Column("validation_conclusion", sa.String(10)),
        sa.Column("bundle_fingerprint", sa.String(128)),
        sa.Column("catalog_source", sa.String(100)),
        sa.Column("catalog_version", sa.String(80)),
        sa.Column("catalog_content_hash", sa.String(64)),
        sa.Column("catalog_effective_at", sa.String(80)),
        sa.Column("catalog_status", sa.String(10)),
        sa.CheckConstraint(
            "validation_conclusion IS NULL OR validation_conclusion IN ('pass', 'warn')",
            name="ck_published_fact_lineage_validation_conclusion",
        ),
        sa.CheckConstraint(
            "catalog_status IS NULL OR catalog_status IN ('active', 'inactive')",
            name="ck_published_fact_lineage_catalog_status",
        ),
        sa.ForeignKeyConstraint(
            ["published_fact_import_id"], ["published_fact_imports.id"]
        ),
        sa.UniqueConstraint(
            "published_fact_import_id",
            name="uq_published_fact_lineage_import",
        ),
    )
    op.create_index(
        "ix_published_fact_lineages_import_id",
        "published_fact_lineages",
        ["published_fact_import_id"],
    )
    op.create_index(
        "ix_published_fact_lineages_fingerprint",
        "published_fact_lineages",
        ["lineage_fingerprint"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(f"""
            CREATE TRIGGER {UPDATE_TRIGGER}
            BEFORE UPDATE ON published_fact_lineages
            BEGIN
                SELECT RAISE(ABORT, 'published fact lineage is immutable');
            END
        """)
        op.execute(f"""
            CREATE TRIGGER {DELETE_TRIGGER}
            BEFORE DELETE ON published_fact_lineages
            BEGIN
                SELECT RAISE(ABORT, 'published fact lineage is immutable');
            END
        """)
    elif dialect == "postgresql":
        op.execute(f"""
            CREATE FUNCTION {POSTGRES_FUNCTION}() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'published fact lineage is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$
        """)
        op.execute(f"""
            CREATE TRIGGER {UPDATE_TRIGGER}
            BEFORE UPDATE ON published_fact_lineages
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
        """)
        op.execute(f"""
            CREATE TRIGGER {DELETE_TRIGGER}
            BEFORE DELETE ON published_fact_lineages
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
        """)
    else:
        raise RuntimeError(
            f"published fact lineage migration does not support dialect {dialect!r}"
        )


def downgrade():
    raise RuntimeError("Baseline lineage migration is forward-only")


