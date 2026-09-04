"""enforce graph version immutability in the database

Revision ID: 0004_graph_version_database_immutability
Revises: 0003_reconcile_legacy_document_schema
"""
from alembic import op

revision = "0004_graph_version_database_immutability"
down_revision = "0003_reconcile_legacy_document_schema"
branch_labels = None
depends_on = None

SQLITE_UPDATE_TRIGGER = "trg_graph_versions_reject_update"
SQLITE_DELETE_TRIGGER = "trg_graph_versions_reject_delete"
POSTGRES_FUNCTION = "reject_graph_version_mutation"


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(f"""
            CREATE TRIGGER {SQLITE_UPDATE_TRIGGER}
            BEFORE UPDATE ON graph_versions
            BEGIN
                SELECT RAISE(ABORT, 'graph_versions are immutable');
            END
        """)
        op.execute(f"""
            CREATE TRIGGER {SQLITE_DELETE_TRIGGER}
            BEFORE DELETE ON graph_versions
            BEGIN
                SELECT RAISE(ABORT, 'graph_versions are immutable');
            END
        """)
        return
    if dialect == "postgresql":
        op.execute(f"""
            CREATE FUNCTION {POSTGRES_FUNCTION}() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'graph_versions are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$
        """)
        op.execute(f"""
            CREATE TRIGGER {SQLITE_UPDATE_TRIGGER}
            BEFORE UPDATE ON graph_versions
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
        """)
        op.execute(f"""
            CREATE TRIGGER {SQLITE_DELETE_TRIGGER}
            BEFORE DELETE ON graph_versions
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
        """)
        return
    raise RuntimeError(
        f"graph version immutability migration does not support dialect {dialect!r}"
    )


def downgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {SQLITE_UPDATE_TRIGGER} ON graph_versions"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {SQLITE_DELETE_TRIGGER} ON graph_versions"
        )
        op.execute(f"DROP FUNCTION IF EXISTS {POSTGRES_FUNCTION}()")
        return
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_UPDATE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_DELETE_TRIGGER}")
