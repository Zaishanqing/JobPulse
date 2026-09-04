"""align legacy lineage columns with the explicit version model"""

from alembic import op
import sqlalchemy as sa


revision = "0021_explicit_lineage_versions"
down_revision = "0020_watermark_config_version"
branch_labels = None
depends_on = None


def _rename_or_add(batch, columns, old, new, type_, *, nullable=False, default=None):
    if new not in columns:
        if old in columns:
            batch.alter_column(old, new_column_name=new, existing_type=type_)
        else:
            batch.add_column(sa.Column(new, type_, nullable=nullable, server_default=default))


def _drop_if_present(batch, columns, name):
    if name in columns:
        batch.drop_column(name)


def _columns(table):
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    with op.batch_alter_table("jd_documents") as batch:
        if "source_version" not in _columns("jd_documents"):
            batch.add_column(sa.Column("source_version", sa.String(64), nullable=True))
        _drop_if_present(batch, _columns("jd_documents"), "content_hash")

    with op.batch_alter_table("published_fact_imports") as batch:
        columns = _columns("published_fact_imports")
        _rename_or_add(batch, columns, "content_hash", "source_version", sa.String(64), nullable=False, default="legacy-source-v1")

    with op.batch_alter_table("release_import_items") as batch:
        columns = _columns("release_import_items")
        _rename_or_add(batch, columns, "content_hash", "source_version", sa.String(64), nullable=False, default="legacy-source-v1")

    with op.batch_alter_table("projection_manifests") as batch:
        columns = _columns("projection_manifests")
        _rename_or_add(batch, columns, "watermark_fingerprint", "watermark_lineage_version", sa.String(64), nullable=False, default="legacy-watermark-v1")
        _rename_or_add(batch, columns, "content_hash", "source_version", sa.String(64), nullable=False, default="legacy-source-v1")
        batch.create_check_constraint("ck_projection_manifests_node_count", "node_count >= 0")
        batch.create_check_constraint("ck_projection_manifests_edge_count", "edge_count >= 0")

    with op.batch_alter_table("build_input_watermarks") as batch:
        columns = _columns("build_input_watermarks")
        _rename_or_add(
            batch,
            columns,
            "lineage_version",
            "lineage_version",
            sa.String(64),
            nullable=False,
            default="legacy-lineage-v1",
        )
        _rename_or_add(batch, columns, "catalog_content_hash", "catalog_source_version", sa.String(64), nullable=False, default="legacy-catalog-v1")
        _drop_if_present(batch, columns, "fingerprint")
        batch.create_check_constraint(
            "ck_build_input_watermarks_validation_state",
            "validation_state IN ('present', 'absent')",
        )
        batch.create_check_constraint(
            "ck_build_input_watermarks_validation_policy",
            "(validation_state = 'present' AND validation_policy_version IS NOT NULL) OR (validation_state = 'absent' AND validation_policy_version IS NULL)",
        )
        batch.create_check_constraint("ck_build_input_watermarks_coverage", "input_coverage >= 0 AND input_coverage <= 1")

    with op.batch_alter_table("published_fact_lineages") as batch:
        columns = _columns("published_fact_lineages")
        _rename_or_add(batch, columns, "lineage_fingerprint", "lineage_lineage_version", sa.String(64), nullable=False, default="legacy-lineage-v1")
        _rename_or_add(batch, columns, "bundle_fingerprint", "bundle_lineage_version", sa.String(128), nullable=True)
        _rename_or_add(batch, columns, "catalog_content_hash", "catalog_source_version", sa.String(64), nullable=True)

    with op.batch_alter_table("relation_claims") as batch:
        columns = _columns("relation_claims")
        _rename_or_add(batch, columns, "catalog_snapshot_fingerprint", "catalog_snapshot_lineage_version", sa.String(64), nullable=False, default="legacy-catalog-v1")
        _rename_or_add(batch, columns, "validation_lineage_fingerprint", "validation_lineage_lineage_version", sa.String(64), nullable=True)
        _rename_or_add(batch, columns, "fingerprint", "lineage_version", sa.String(64), nullable=False, default="legacy-lineage-v1")
        batch.create_check_constraint("ck_relation_claims_kind", "claim_kind IN ('observed', 'reviewed')")
        batch.create_check_constraint("ck_relation_claims_source_kind", "source_kind IN ('published_fact', 'legacy_local')")

    with op.batch_alter_table("graph_versions") as batch:
        batch.alter_column("source_version", server_default=None, existing_type=sa.String(64))

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        immutable = (
            "build_input_watermarks", "dependency_analysis_runs", "dependency_candidates",
            "dependency_review_decisions", "effective_mapping_records",
            "graph_version_dependencies", "graph_versions", "mapping_review_decisions",
            "projection_manifests", "published_fact_lineages", "published_fact_release_links",
            "relation_claims", "release_import_batches", "release_import_items",
        )
        for table in immutable:
            for action in ("update", "delete"):
                name = f"trg_{table}_reject_{action}"
                op.execute(f"DROP TRIGGER IF EXISTS {name}")
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {action.upper()} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END"
                )


def downgrade() -> None:
    raise RuntimeError("Migration 0021 is forward-only")
