"""explicit source identity and idempotency

Aligns the main-system schema with the explicit identity/version model:
business hashes and fingerprints are replaced by explicit source versions,
request ids and idempotency keys.

Revision ID: c25a72547e98
Revises: 20260805_62
Create Date: 2026-08-05 23:10:59.233635
"""

from alembic import op
import sqlalchemy as sa


revision: str = "c25a72547e98"
down_revision: str | None = "20260805_62"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _unique_names(table: str) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_unique_constraints(table)
    }


def _add_columns(table: str, columns: list[tuple[str, sa.Column]]) -> None:
    existing = _columns(table)
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name, column in columns:
            if name not in existing:
                batch_op.add_column(column)


def _drop_columns(table: str, names: list[str]) -> None:
    existing = _columns(table)
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name in names:
            if name in existing:
                batch_op.drop_column(name)


def _drop_unique_if_present(table: str, name: str) -> None:
    if name not in _unique_names(table):
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.drop_constraint(name, type_="unique")


def _create_unique_if_missing(table: str, name: str, columns: list[str]) -> None:
    if name in _unique_names(table):
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.create_unique_constraint(name, columns)


def _set_not_null(
    table: str, column: str, type_: sa.types.TypeEngine, server_default: str | None = None
) -> None:
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.alter_column(
            column,
            existing_type=type_,
            nullable=False,
            server_default=server_default,
        )


def _backfill(table: str, target: str, source: str) -> None:
    """Copy an existing opaque identifier into the new explicit column."""
    if target not in _columns(table) or source not in _columns(table):
        return
    op.execute(f"UPDATE {table} SET {target} = {source} WHERE {source} IS NOT NULL")


def _drop_sqlite_admission_trigger() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS validated_bundle_snapshots_validate_insert"
        )


def _recreate_sqlite_admission_trigger() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS
        validated_bundle_snapshots_validate_insert
        BEFORE INSERT ON validated_bundle_snapshots
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1
                FROM validation_reports AS report
                JOIN data_validation_tasks AS task
                  ON task.id = report.data_validation_task_id
                JOIN extraction_tasks AS extraction
                  ON extraction.id = task.extraction_task_id
                WHERE report.id = NEW.validation_report_id
                  AND report.conclusion IN ('pass', 'warn')
                  AND report.data_validation_task_id =
                      NEW.data_validation_task_id
                  AND report.conclusion = NEW.validation_conclusion
                  AND task.status = 'succeeded'
                  AND task.extraction_task_id = NEW.extraction_task_id
                  AND task.source_jd_version_id =
                      NEW.source_jd_version_id
                  AND task.bundle_id =
                      NEW.bundle_id
                  AND extraction.source_jd_version_id =
                      NEW.source_jd_version_id
            ) THEN RAISE(
                ABORT,
                'ValidatedBundleSnapshot admission or lineage is invalid'
            ) END;
        END
        """
    )


def _drop_immutability_triggers(table: str, message: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_update")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_delete")
    elif dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_mutation ON {table}")


def _recreate_immutability_triggers(table: str, message: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            f"CREATE TRIGGER {table}_reject_update "
            f"BEFORE UPDATE ON {table} BEGIN "
            f"SELECT RAISE(ABORT, '{message}'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_reject_delete "
            f"BEFORE DELETE ON {table} BEGIN "
            f"SELECT RAISE(ABORT, '{message}'); END"
        )
    elif dialect == "postgresql":
        function = {
            "source_jd_versions": "reject_source_jd_version_mutation",
            "validated_bundle_snapshots": "reject_validated_bundle_snapshot_mutation",
            "jd_publications": "reject_jd_publication_mutation",
            "source_cv_versions": "reject_cv_immutable_mutation",
        }[table]
        op.execute(
            f"CREATE TRIGGER {table}_reject_mutation "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )


def upgrade() -> None:
    """Upgrade schema."""
    _drop_sqlite_admission_trigger()

    _add_columns(
        "cv_extraction_tasks",
        [
            ("request_id", sa.Column("request_id", sa.String(length=128), nullable=True)),
            ("execution_id", sa.Column("execution_id", sa.String(length=128), nullable=True)),
            ("review_id", sa.Column("review_id", sa.String(length=128), nullable=True)),
            (
                "confirmation_idempotency_id",
                sa.Column(
                    "confirmation_idempotency_id", sa.String(length=128), nullable=True
                ),
            ),
        ],
    )
    _backfill("cv_extraction_tasks", "request_id", "request_fingerprint")
    if "request_id" in _columns("cv_extraction_tasks"):
        _set_not_null("cv_extraction_tasks", "request_id", sa.String(length=128))
    _drop_unique_if_present("cv_extraction_tasks", "uq_cv_extraction_tasks_natural_key")
    _create_unique_if_missing(
        "cv_extraction_tasks",
        "uq_cv_extraction_tasks_natural_key",
        ["source_cv_version_id", "request_id"],
    )
    _drop_columns(
        "cv_extraction_tasks",
        [
            "execution_fingerprint",
            "confirmation_idempotency_fingerprint",
            "request_fingerprint",
            "review_fingerprint",
        ],
    )

    _add_columns(
        "data_validation_tasks",
        [("bundle_id", sa.Column("bundle_id", sa.String(length=71), nullable=True))],
    )
    _backfill("data_validation_tasks", "bundle_id", "bundle_fingerprint")
    if "bundle_id" in _columns("data_validation_tasks"):
        _set_not_null("data_validation_tasks", "bundle_id", sa.String(length=71))
    _drop_unique_if_present("data_validation_tasks", "uq_data_validation_tasks_natural_key")
    _create_unique_if_missing(
        "data_validation_tasks",
        "uq_data_validation_tasks_natural_key",
        ["extraction_task_id", "bundle_id", "policy_version"],
    )
    _drop_columns("data_validation_tasks", ["bundle_fingerprint"])

    _drop_columns("emerging_positions", ["approved_definition_hash"])

    _add_columns(
        "extraction_tasks",
        [("request_id", sa.Column("request_id", sa.String(length=128), nullable=True))],
    )
    _backfill("extraction_tasks", "request_id", "request_fingerprint")
    if "request_id" in _columns("extraction_tasks"):
        _set_not_null("extraction_tasks", "request_id", sa.String(length=128))
    _drop_unique_if_present("extraction_tasks", "uq_extraction_tasks_version_fingerprint")
    _create_unique_if_missing(
        "extraction_tasks",
        "uq_extraction_tasks_version_request",
        ["source_jd_version_id", "request_id"],
    )
    _drop_columns("extraction_tasks", ["request_fingerprint"])

    _drop_immutability_triggers(
        "jd_publications", "JDPublication records are immutable"
    )
    if "content_fingerprint" in _columns("jd_publications"):
        with op.batch_alter_table("jd_publications", schema=None) as batch_op:
            batch_op.drop_index(batch_op.f("ix_jd_publications_content_fingerprint"))
            batch_op.drop_column("content_fingerprint")
    _recreate_immutability_triggers(
        "jd_publications", "JDPublication records are immutable"
    )

    if "payload_hash" in _columns("knowledge_graph_entity_mappings"):
        with op.batch_alter_table(
            "knowledge_graph_entity_mappings", schema=None
        ) as batch_op:
            batch_op.alter_column(
                "payload_hash",
                existing_type=sa.VARCHAR(length=64),
                type_=sa.String(length=200),
                existing_nullable=True,
            )

    _add_columns(
        "matching_service_references",
        [
            ("tenant_id", sa.Column("tenant_id", sa.String(length=128), nullable=True)),
            (
                "idempotency_key",
                sa.Column("idempotency_key", sa.String(length=512), nullable=True),
            ),
            (
                "cv_profile_version",
                sa.Column("cv_profile_version", sa.String(length=200), nullable=True),
            ),
            (
                "position_profile_version",
                sa.Column(
                    "position_profile_version", sa.String(length=200), nullable=True
                ),
            ),
        ],
    )
    _backfill(
        "matching_service_references",
        "idempotency_key",
        "idempotency_key_hash",
    )
    if "tenant_id" in _columns("matching_service_references"):
        _set_not_null(
            "matching_service_references",
            "tenant_id",
            sa.String(length=128),
            server_default="legacy-tenant-v1",
        )
    if "idempotency_key" in _columns("matching_service_references"):
        _set_not_null(
            "matching_service_references", "idempotency_key", sa.String(length=512)
        )
    if "cv_profile_version" in _columns("matching_service_references"):
        _set_not_null(
            "matching_service_references",
            "cv_profile_version",
            sa.String(length=200),
            server_default="",
        )
    if "position_profile_version" in _columns("matching_service_references"):
        _set_not_null(
            "matching_service_references",
            "position_profile_version",
            sa.String(length=200),
            server_default="",
        )
    _drop_unique_if_present(
        "matching_service_references", "uq_matching_service_reference_idempotency"
    )
    _create_unique_if_missing(
        "matching_service_references",
        "uq_matching_service_reference_idempotency",
        ["user_id", "idempotency_key"],
    )
    _drop_columns(
        "matching_service_references",
        [
            "position_profile_fingerprint",
            "idempotency_key_hash",
            "cv_profile_fingerprint",
            "tenant_ref",
        ],
    )

    _add_columns(
        "matching_submission_intents",
        [
            (
                "idempotency_key",
                sa.Column("idempotency_key", sa.String(length=512), nullable=True),
            ),
            ("tenant_id", sa.Column("tenant_id", sa.String(length=128), nullable=True)),
            (
                "cv_profile_version",
                sa.Column("cv_profile_version", sa.String(length=200), nullable=True),
            ),
            (
                "position_profile_version",
                sa.Column(
                    "position_profile_version", sa.String(length=200), nullable=True
                ),
            ),
        ],
    )
    _backfill("matching_submission_intents", "idempotency_key", "idempotency_key_hash")
    if "idempotency_key" in _columns("matching_submission_intents"):
        _set_not_null(
            "matching_submission_intents", "idempotency_key", sa.String(length=512)
        )
    if "tenant_id" in _columns("matching_submission_intents"):
        _set_not_null(
            "matching_submission_intents",
            "tenant_id",
            sa.String(length=128),
            server_default="legacy-tenant-v1",
        )
    if "cv_profile_version" in _columns("matching_submission_intents"):
        _set_not_null(
            "matching_submission_intents",
            "cv_profile_version",
            sa.String(length=200),
            server_default="legacy-profile-v1",
        )
    if "position_profile_version" in _columns("matching_submission_intents"):
        _set_not_null(
            "matching_submission_intents",
            "position_profile_version",
            sa.String(length=200),
            server_default="legacy-profile-v1",
        )
    _drop_unique_if_present("matching_submission_intents", "uq_intent_idempotency_key_hash")
    _create_unique_if_missing(
        "matching_submission_intents", "uq_intent_idempotency_key", ["idempotency_key"]
    )
    _drop_columns(
        "matching_submission_intents",
        [
            "position_profile_fingerprint",
            "idempotency_key_hash",
            "cv_profile_fingerprint",
            "tenant_ref",
        ],
    )

    _drop_unique_if_present(
        "predicted_position_definition_versions", "uq_prediction_definition_fingerprint"
    )
    _drop_columns("predicted_position_definition_versions", ["input_fingerprint"])

    if "input_fingerprint" in _columns("predicted_position_matches"):
        with op.batch_alter_table("predicted_position_matches", schema=None) as batch_op:
            batch_op.drop_index(
                batch_op.f("ix_predicted_position_matches_input_fingerprint")
            )
            batch_op.drop_column("input_fingerprint")

    # The CV immutability trigger predates the explicit version column, so it
    # must be released for the legacy backfill and reinstalled afterwards.
    _drop_immutability_triggers(
        "source_cv_versions", "source_cv_versions records are immutable"
    )
    _add_columns(
        "source_cv_versions",
        [
            (
                "source_version",
                sa.Column("source_version", sa.String(length=64), nullable=True),
            )
        ],
    )
    # Derive a stable per-row version for legacy rows so the new unique
    # constraint holds; the value is an opaque legacy version label.
    op.execute(
        "UPDATE source_cv_versions SET source_version = "
        "'legacy-' || substr(id, 1, 32) "
        "WHERE source_version IS NULL"
    )
    if "source_version" in _columns("source_cv_versions"):
        _set_not_null("source_cv_versions", "source_version", sa.String(length=64))
    _drop_unique_if_present("source_cv_versions", "uq_source_cv_versions_content")
    _create_unique_if_missing(
        "source_cv_versions",
        "uq_source_cv_versions_version",
        ["source_cv_id", "source_version"],
    )
    _drop_columns(
        "source_cv_versions",
        ["raw_text_sha256", "source_file_sha256", "content_hash"],
    )
    _recreate_immutability_triggers(
        "source_cv_versions", "source_cv_versions records are immutable"
    )

    _drop_immutability_triggers(
        "source_jd_versions", "SourceJDVersion records are immutable"
    )
    _add_columns(
        "source_jd_versions",
        [
            (
                "source_version",
                sa.Column("source_version", sa.String(length=128), nullable=True),
            )
        ],
    )
    _backfill("source_jd_versions", "source_version", "content_hash")
    if "source_version" in _columns("source_jd_versions"):
        _set_not_null("source_jd_versions", "source_version", sa.String(length=128))
    _drop_unique_if_present("source_jd_versions", "uq_source_jd_versions_source_hash")
    _create_unique_if_missing(
        "source_jd_versions",
        "uq_source_jd_versions_source_version",
        ["source_jd_id", "source_version"],
    )
    _drop_columns("source_jd_versions", ["content_hash"])
    _recreate_immutability_triggers(
        "source_jd_versions", "SourceJDVersion records are immutable"
    )

    _drop_columns("trend_reports", ["input_fingerprint"])

    _drop_columns("trend_sources", ["content_hash"])

    _drop_immutability_triggers(
        "validated_bundle_snapshots",
        "ValidatedBundleSnapshot records are immutable",
    )
    _add_columns(
        "validated_bundle_snapshots",
        [("bundle_id", sa.Column("bundle_id", sa.String(length=71), nullable=True))],
    )
    _backfill("validated_bundle_snapshots", "bundle_id", "bundle_fingerprint")
    if "bundle_id" in _columns("validated_bundle_snapshots"):
        _set_not_null("validated_bundle_snapshots", "bundle_id", sa.String(length=71))
    _drop_columns("validated_bundle_snapshots", ["bundle_fingerprint"])
    _recreate_immutability_triggers(
        "validated_bundle_snapshots",
        "ValidatedBundleSnapshot records are immutable",
    )

    _drop_columns(
        "validated_cv_snapshots",
        ["raw_text_sha256", "source_file_sha256", "content_fingerprint"],
    )

    _recreate_sqlite_admission_trigger()


def downgrade() -> None:
    """Downgrade schema."""
    _drop_sqlite_admission_trigger()

    with op.batch_alter_table("validated_cv_snapshots", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("content_fingerprint", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_file_sha256", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.add_column(
            sa.Column("raw_text_sha256", sa.VARCHAR(length=71), nullable=True)
        )

    _drop_immutability_triggers(
        "validated_bundle_snapshots",
        "ValidatedBundleSnapshot records are immutable",
    )
    with op.batch_alter_table("validated_bundle_snapshots", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("bundle_fingerprint", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.drop_column("bundle_id")
    _recreate_immutability_triggers(
        "validated_bundle_snapshots",
        "ValidatedBundleSnapshot records are immutable",
    )

    with op.batch_alter_table("trend_sources", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("content_hash", sa.VARCHAR(length=64), nullable=True)
        )

    with op.batch_alter_table("trend_reports", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("input_fingerprint", sa.VARCHAR(length=64), nullable=True)
        )

    _drop_immutability_triggers(
        "source_jd_versions", "SourceJDVersion records are immutable"
    )
    with op.batch_alter_table("source_jd_versions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("content_hash", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.drop_constraint(
            "uq_source_jd_versions_source_version", type_="unique"
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_source_jd_versions_source_hash"),
            ["source_jd_id", "content_hash"],
        )
        batch_op.drop_column("source_version")
    _recreate_immutability_triggers(
        "source_jd_versions", "SourceJDVersion records are immutable"
    )

    with op.batch_alter_table("source_cv_versions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("content_hash", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_file_sha256", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.add_column(
            sa.Column("raw_text_sha256", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.drop_constraint("uq_source_cv_versions_version", type_="unique")
        batch_op.create_unique_constraint(
            batch_op.f("uq_source_cv_versions_content"),
            ["source_cv_id", "content_hash"],
        )
        batch_op.drop_column("source_version")

    with op.batch_alter_table("predicted_position_matches", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("input_fingerprint", sa.VARCHAR(length=64), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_predicted_position_matches_input_fingerprint"),
            ["input_fingerprint"],
            unique=False,
        )

    with op.batch_alter_table(
        "predicted_position_definition_versions", schema=None
    ) as batch_op:
        batch_op.add_column(
            sa.Column("input_fingerprint", sa.VARCHAR(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_prediction_definition_fingerprint"),
            ["predicted_position_id", "input_fingerprint"],
        )

    with op.batch_alter_table("matching_submission_intents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "tenant_ref",
                sa.VARCHAR(length=64),
                nullable=False,
                server_default="legacy-tenant-ref",
            )
        )
        batch_op.add_column(
            sa.Column(
                "cv_profile_fingerprint",
                sa.VARCHAR(length=64),
                nullable=False,
                server_default="legacy-profile",
            )
        )
        batch_op.add_column(
            sa.Column(
                "idempotency_key_hash",
                sa.VARCHAR(length=64),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "position_profile_fingerprint",
                sa.VARCHAR(length=64),
                nullable=False,
                server_default="legacy-profile",
            )
        )
        batch_op.drop_constraint("uq_intent_idempotency_key", type_="unique")
        batch_op.create_unique_constraint(
            batch_op.f("uq_intent_idempotency_key_hash"),
            ["idempotency_key_hash"],
        )
        batch_op.drop_column("position_profile_version")
        batch_op.drop_column("cv_profile_version")
        batch_op.drop_column("tenant_id")
        batch_op.drop_column("idempotency_key")

    with op.batch_alter_table("matching_service_references", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "tenant_ref",
                sa.VARCHAR(length=64),
                nullable=False,
                server_default="legacy-tenant-ref",
            )
        )
        batch_op.add_column(
            sa.Column(
                "cv_profile_fingerprint",
                sa.VARCHAR(length=64),
                nullable=False,
                server_default="legacy-profile",
            )
        )
        batch_op.add_column(
            sa.Column(
                "idempotency_key_hash",
                sa.VARCHAR(length=64),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "position_profile_fingerprint",
                sa.VARCHAR(length=64),
                nullable=False,
                server_default="legacy-profile",
            )
        )
        batch_op.drop_constraint(
            "uq_matching_service_reference_idempotency", type_="unique"
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_matching_service_reference_idempotency"),
            ["user_id", "idempotency_key_hash"],
        )
        batch_op.drop_column("position_profile_version")
        batch_op.drop_column("cv_profile_version")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("tenant_id")

    with op.batch_alter_table("knowledge_graph_entity_mappings", schema=None) as batch_op:
        batch_op.alter_column(
            "payload_hash",
            existing_type=sa.String(length=200),
            type_=sa.VARCHAR(length=64),
            existing_nullable=True,
        )

    _drop_immutability_triggers(
        "jd_publications", "JDPublication records are immutable"
    )
    with op.batch_alter_table("jd_publications", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "content_fingerprint",
                sa.VARCHAR(length=71),
                nullable=False,
                server_default="legacy-content",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_jd_publications_content_fingerprint"),
            ["content_fingerprint"],
            unique=False,
        )
    _recreate_immutability_triggers(
        "jd_publications", "JDPublication records are immutable"
    )

    with op.batch_alter_table("extraction_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "request_fingerprint",
                sa.VARCHAR(length=71),
                nullable=False,
                server_default="legacy-request",
            )
        )
        batch_op.drop_constraint("uq_extraction_tasks_version_request", type_="unique")
        batch_op.create_unique_constraint(
            batch_op.f("uq_extraction_tasks_version_fingerprint"),
            ["source_jd_version_id", "request_fingerprint"],
        )
        batch_op.drop_column("request_id")

    with op.batch_alter_table("emerging_positions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("approved_definition_hash", sa.VARCHAR(length=64), nullable=True)
        )

    with op.batch_alter_table("data_validation_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "bundle_fingerprint",
                sa.VARCHAR(length=71),
                nullable=True,
            )
        )
        batch_op.drop_constraint(
            "uq_data_validation_tasks_natural_key", type_="unique"
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_data_validation_tasks_natural_key"),
            ["extraction_task_id", "bundle_fingerprint", "policy_version"],
        )
        batch_op.drop_column("bundle_id")

    with op.batch_alter_table("cv_extraction_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("review_fingerprint", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "request_fingerprint",
                sa.VARCHAR(length=128),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "confirmation_idempotency_fingerprint",
                sa.VARCHAR(length=71),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("execution_fingerprint", sa.VARCHAR(length=71), nullable=True)
        )
        batch_op.drop_constraint(
            "uq_cv_extraction_tasks_natural_key", type_="unique"
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_cv_extraction_tasks_natural_key"),
            ["source_cv_version_id", "request_fingerprint"],
        )
        batch_op.drop_column("confirmation_idempotency_id")
        batch_op.drop_column("review_id")
        batch_op.drop_column("execution_id")
        batch_op.drop_column("request_id")

    _recreate_sqlite_admission_trigger()
