"""enforce trusted graph workflow after independent audit

Revision ID: 0002_trusted_graph_workflow
Revises: 0001_initial
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0002_trusted_graph_workflow"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}

def _foreign_key_columns(table: str) -> set[tuple[str, ...]]:
    return {tuple(item["constrained_columns"]) for item in sa.inspect(op.get_bind()).get_foreign_keys(table)}

def _unique_columns(table: str) -> set[tuple[str, ...]]:
    inspector = sa.inspect(op.get_bind())
    values = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table)}
    values.update(tuple(item["column_names"]) for item in inspector.get_indexes(table) if item.get("unique"))
    return values

def _named_constraints(table: str) -> tuple[set[str], set[str]]:
    inspector = sa.inspect(op.get_bind())
    foreign_keys = {
        item["name"] for item in inspector.get_foreign_keys(table) if item.get("name")
    }
    unique_constraints = {
        item["name"]
        for item in inspector.get_unique_constraints(table)
        if item.get("name")
    }
    return foreign_keys, unique_constraints

def upgrade():
    bind = op.get_bind()
    # Alembic creates version_num as VARCHAR(32), while several preserved
    # historical revision identifiers in this chain are longer than 32 chars.
    # SQLite does not enforce that width; PostgreSQL does, so widen it before
    # Alembic records the next revision.
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE alembic_version "
            "ALTER COLUMN version_num TYPE VARCHAR(128)"
        )
    if "enterprise_name" not in _columns("jd_documents"):
        op.add_column("jd_documents", sa.Column("enterprise_name", sa.String(160), nullable=True))

    if ("document_id", "requirement_id") not in _unique_columns("extracted_candidate_requirements"):
        with op.batch_alter_table("extracted_candidate_requirements") as batch: batch.create_unique_constraint("uq_extracted_candidate_document_requirement", ["document_id", "requirement_id"])
    if ("normalized_record_id", "requirement_id") not in _unique_columns("normalized_requirement_records"):
        with op.batch_alter_table("normalized_requirement_records") as batch: batch.create_unique_constraint("uq_normalized_record_requirement", ["normalized_record_id", "requirement_id"])

    unresolved = _columns("unresolved_normalization_items")
    with op.batch_alter_table("unresolved_normalization_items") as batch:
        if "reviewer_id" not in unresolved: batch.add_column(sa.Column("reviewer_id", sa.Integer(), nullable=True))
        if "reviewed_at" not in unresolved: batch.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        if "review_reason" not in unresolved: batch.add_column(sa.Column("review_reason", sa.Text(), nullable=True))
    if ("reviewer_id",) not in _foreign_key_columns("unresolved_normalization_items"):
        with op.batch_alter_table("unresolved_normalization_items") as batch: batch.create_foreign_key("fk_unresolved_reviewer", "users", ["reviewer_id"], ["id"])

    support = _columns("position_skill_supports")
    with op.batch_alter_table("position_skill_supports") as batch:
        if "source_requirement_id" not in support: batch.add_column(sa.Column("source_requirement_id", sa.Integer(), nullable=True))
        if "extraction_record_id" not in support: batch.add_column(sa.Column("extraction_record_id", sa.Integer(), nullable=True))
    # Older demo databases stored only extraction JSON. Materialize the latest payload before
    # adding support-chain foreign keys; no source data is discarded.
    extraction_rows = bind.execute(sa.text("SELECT id, document_id, payload FROM jd_extraction_records ORDER BY id DESC")).mappings()
    seen_documents: set[str] = set()
    for row in extraction_rows:
        if row["document_id"] in seen_documents: continue
        seen_documents.add(row["document_id"])
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        for item in payload.get("responsibilities", []):
            exists = bind.execute(sa.text("SELECT 1 FROM extracted_task_requirements WHERE document_id=:document_id AND requirement_id=:requirement_id LIMIT 1"), {"document_id": row["document_id"], "requirement_id": item["requirement_id"]}).first()
            if not exists: bind.execute(sa.text("INSERT INTO extracted_task_requirements (created_at, document_id, requirement_id, payload) VALUES (CURRENT_TIMESTAMP, :document_id, :requirement_id, :payload)"), {"document_id": row["document_id"], "requirement_id": item["requirement_id"], "payload": json.dumps(item, ensure_ascii=False)})
        for item in payload.get("requirements", []):
            exists = bind.execute(sa.text("SELECT 1 FROM extracted_candidate_requirements WHERE document_id=:document_id AND requirement_id=:requirement_id LIMIT 1"), {"document_id": row["document_id"], "requirement_id": item["requirement_id"]}).first()
            if not exists: bind.execute(sa.text("INSERT INTO extracted_candidate_requirements (created_at, document_id, requirement_id, kind, modality, payload) VALUES (CURRENT_TIMESTAMP, :document_id, :requirement_id, :kind, :modality, :payload)"), {"document_id": row["document_id"], "requirement_id": item["requirement_id"], "kind": item["kind"], "modality": item.get("modality", "unknown"), "payload": json.dumps(item, ensure_ascii=False)})
        for table, key, values in (("extracted_company_facts", "fact_id", payload.get("company_facts", [])), ("extracted_employment_facts", "fact_id", payload.get("employment_facts", []))):
            for item in values:
                exists = bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE document_id=:document_id AND {key}=:item_id LIMIT 1"), {"document_id": row["document_id"], "item_id": item[key]}).first()
                if not exists: bind.execute(sa.text(f"INSERT INTO {table} (created_at, document_id, {key}, payload) VALUES (CURRENT_TIMESTAMP, :document_id, :item_id, :payload)"), {"document_id": row["document_id"], "item_id": item[key], "payload": json.dumps(item, ensure_ascii=False)})
    bind.execute(sa.text("""UPDATE position_skill_supports SET source_requirement_id=(SELECT e.id FROM extracted_candidate_requirements e WHERE e.document_id=position_skill_supports.document_id AND e.requirement_id=position_skill_supports.requirement_id ORDER BY e.id DESC LIMIT 1) WHERE source_requirement_id IS NULL"""))
    bind.execute(sa.text("""UPDATE position_skill_supports SET extraction_record_id=(SELECT e.id FROM jd_extraction_records e WHERE e.document_id=position_skill_supports.document_id ORDER BY e.id DESC LIMIT 1) WHERE extraction_record_id IS NULL"""))
    invalid = bind.execute(sa.text("SELECT COUNT(*) FROM position_skill_supports WHERE source_requirement_id IS NULL OR extraction_record_id IS NULL")).scalar_one()
    if invalid: raise RuntimeError("cannot migrate invalid PositionSkillSupport rows; repair their source requirement and extraction record references first")
    support_fks = _foreign_key_columns("position_skill_supports")
    fk_specs = [
        (("position_id",), "fk_support_position", "standard_positions", ["position_id"], ["position_id"]),
        (("skill_id",), "fk_support_skill", "skills", ["skill_id"], ["skill_id"]),
        (("document_id",), "fk_support_document", "jd_documents", ["document_id"], ["document_id"]),
        (("normalized_skill_id",), "fk_support_normalized_skill", "normalized_skill_records", ["normalized_skill_id"], ["id"]),
        (("evidence_id",), "fk_support_evidence", "extraction_evidence", ["evidence_id"], ["id"]),
        (("source_requirement_id",), "fk_support_source_requirement", "extracted_candidate_requirements", ["source_requirement_id"], ["id"]),
        (("extraction_record_id",), "fk_support_extraction", "jd_extraction_records", ["extraction_record_id"], ["id"]),
    ]
    with op.batch_alter_table("position_skill_supports") as batch:
        batch.alter_column("source_requirement_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("extraction_record_id", existing_type=sa.Integer(), nullable=False)
        for columns, name, target, local, remote in fk_specs:
            if columns not in support_fks: batch.create_foreign_key(name, target, local, remote)
        if ("build_run_id", "normalized_skill_id", "evidence_id") not in _unique_columns("position_skill_supports"): batch.create_unique_constraint("uq_support_build_normalized_evidence", ["build_run_id", "normalized_skill_id", "evidence_id"])

    relation = _columns("position_skill_relation_drafts")
    if "status" not in relation: op.add_column("position_skill_relation_drafts", sa.Column("status", sa.String(30), nullable=False, server_default="in_review"))
    relation_fks = _foreign_key_columns("position_skill_relation_drafts")
    with op.batch_alter_table("position_skill_relation_drafts") as batch:
        if ("position_id",) not in relation_fks: batch.create_foreign_key("fk_relation_position", "standard_positions", ["position_id"], ["position_id"])
        if ("skill_id",) not in relation_fks: batch.create_foreign_key("fk_relation_skill", "skills", ["skill_id"], ["skill_id"])

    review = _columns("review_tasks")
    if "build_run_id" not in review: op.add_column("review_tasks", sa.Column("build_run_id", sa.Integer(), nullable=True))
    bind.execute(sa.text("UPDATE review_tasks SET status='pending' WHERE status='open'"))
    review_fks = _foreign_key_columns("review_tasks")
    with op.batch_alter_table("review_tasks") as batch:
        if ("build_run_id",) not in review_fks: batch.create_foreign_key("fk_review_build", "graph_build_runs", ["build_run_id"], ["id"])
        if ("assignee_id",) not in review_fks: batch.create_foreign_key("fk_review_assignee", "users", ["assignee_id"], ["id"])

    version = _columns("graph_versions")
    additions = [("build_run_id", sa.Integer()), ("version_name", sa.String(80)), ("content_hash", sa.String(64)), ("published_at", sa.DateTime(timezone=True))]
    with op.batch_alter_table("graph_versions") as batch:
        for name, type_ in additions:
            if name not in version: batch.add_column(sa.Column(name, type_, nullable=True))
    rows = bind.execute(sa.text("SELECT id, version_number, snapshot, created_at FROM graph_versions")).mappings()
    for row in rows:
        snapshot = row["snapshot"] if isinstance(row["snapshot"], dict) else json.loads(row["snapshot"])
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        bind.execute(sa.text("UPDATE graph_versions SET version_name=:name, content_hash=:digest, published_at=COALESCE(published_at, created_at) WHERE id=:id"), {"name": f"v{row['version_number']}", "digest": digest, "id": row["id"]})
    version_fks = _foreign_key_columns("graph_versions")
    with op.batch_alter_table("graph_versions") as batch:
        batch.alter_column("version_name", existing_type=sa.String(80), nullable=False)
        batch.alter_column("content_hash", existing_type=sa.String(64), nullable=False)
        batch.alter_column("published_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        if ("build_run_id",) not in version_fks: batch.create_foreign_key("fk_version_build", "graph_build_runs", ["build_run_id"], ["id"])
        if ("published_by",) not in version_fks: batch.create_foreign_key("fk_version_publisher", "users", ["published_by"], ["id"])
        if ("position_id", "version_number") not in _unique_columns("graph_versions"): batch.create_unique_constraint("uq_graph_version_number", ["position_id", "version_number"])
        if ("position_id", "version_name") not in _unique_columns("graph_versions"): batch.create_unique_constraint("uq_graph_version_name", ["position_id", "version_name"])

    audit = _columns("audit_logs")
    with op.batch_alter_table("audit_logs") as batch:
        if "before" in audit and "before_snapshot" not in audit: batch.alter_column("before", new_column_name="before_snapshot", existing_type=sa.JSON())
        if "after" in audit and "after_snapshot" not in audit: batch.alter_column("after", new_column_name="after_snapshot", existing_type=sa.JSON())
    if ("actor_id",) not in _foreign_key_columns("audit_logs"):
        with op.batch_alter_table("audit_logs") as batch: batch.create_foreign_key("fk_audit_actor", "users", ["actor_id"], ["id"])
    bind.execute(sa.text("UPDATE graph_build_runs SET status='succeeded' WHERE status IN ('draft','reviewed','published')"))

def downgrade():
    # Revision 0001 creates the current model metadata dynamically, so on a
    # clean database most 0002 column operations are intentionally no-ops.
    # Reverse only explicitly named constraints that 0002 creates while
    # preserving reconciled business data and the 0001 model-shaped schema.
    specifications = {
        "extracted_candidate_requirements": (
            (), ("uq_extracted_candidate_document_requirement",),
        ),
        "normalized_requirement_records": (
            (), ("uq_normalized_record_requirement",),
        ),
        "unresolved_normalization_items": (("fk_unresolved_reviewer",), ()),
        "position_skill_supports": (
            (
                "fk_support_position", "fk_support_skill", "fk_support_document",
                "fk_support_normalized_skill", "fk_support_evidence",
                "fk_support_source_requirement", "fk_support_extraction",
            ),
            ("uq_support_build_normalized_evidence",),
        ),
        "position_skill_relation_drafts": (
            ("fk_relation_position", "fk_relation_skill"), (),
        ),
        "review_tasks": (("fk_review_build", "fk_review_assignee"), ()),
        "graph_versions": (
            ("fk_version_build", "fk_version_publisher"),
            ("uq_graph_version_number", "uq_graph_version_name"),
        ),
        "audit_logs": (("fk_audit_actor",), ()),
    }
    for table, (foreign_key_names, unique_names) in specifications.items():
        existing_foreign_keys, existing_unique_constraints = _named_constraints(table)
        owned_foreign_keys = [
            name for name in foreign_key_names if name in existing_foreign_keys
        ]
        owned_unique_constraints = [
            name for name in unique_names if name in existing_unique_constraints
        ]
        if not owned_foreign_keys and not owned_unique_constraints:
            continue
        with op.batch_alter_table(table) as batch:
            for name in owned_foreign_keys:
                batch.drop_constraint(name, type_="foreignkey")
            for name in owned_unique_constraints:
                batch.drop_constraint(name, type_="unique")
