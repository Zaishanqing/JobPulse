"""make structured extraction facts the complete authoritative projection

Revision ID: 0006_structured_extraction_authority
Revises: 0005_core_relationship_constraints
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "0006_structured_extraction_authority"
down_revision = "0005_core_relationship_constraints"
branch_labels = None
depends_on = None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _without_evidence(value: dict) -> dict:
    result = dict(value)
    result.pop("evidence", None)
    return result


def _latest_payloads(bind, records):
    latest = sa.select(
        records.c.document_id, sa.func.max(records.c.id).label("id")
    ).group_by(records.c.document_id).subquery()
    return bind.execute(
        sa.select(records.c.document_id, records.c.payload).join(
            latest, records.c.id == latest.c.id
        )
    ).mappings().all()


def _insert_missing(bind, table, key: dict, values: dict) -> None:
    exists = bind.execute(sa.select(table.c.id).where(
        *(table.c[name] == value for name, value in key.items())
    )).scalar_one_or_none()
    if exists is None:
        bind.execute(table.insert().values(
            **key, **values, created_at=datetime.now(timezone.utc)
        ))


def _canonical_evidence(bind, evidence, document_id, owner_type, owner_ref, value):
    embedded = (value or {}).get("evidence") or {}
    candidates = bind.execute(sa.select(evidence).where(
        evidence.c.document_id == document_id,
        evidence.c.owner_ref.in_((owner_ref, "root") if owner_type == "job_title" else (owner_ref,)),
    ).order_by(evidence.c.id)).mappings().all()
    canonical = next((row for row in candidates if
        row["owner_type"] == owner_type and row["owner_ref"] == owner_ref), None)
    selected = canonical or (candidates[0] if candidates else None)
    if selected is not None:
        bind.execute(evidence.update().where(evidence.c.id == selected["id"]).values(
            owner_type=owner_type, owner_ref=owner_ref
        ))
        for duplicate in candidates:
            if duplicate["id"] != selected["id"]:
                bind.execute(evidence.delete().where(evidence.c.id == duplicate["id"]))
        return
    if embedded.get("quote") is None:
        return
    bind.execute(evidence.insert().values(
        document_id=document_id, owner_type=owner_type, owner_ref=owner_ref,
        quote=embedded["quote"], start=embedded.get("start"), end=embedded.get("end"),
        alignment=embedded.get("alignment", "unresolved"),
        occurrence_index=embedded.get("occurrence_index"),
        created_at=datetime.now(timezone.utc),
    ))


def _deduplicate(bind, table, columns):
    rows = bind.execute(sa.select(table).order_by(table.c.id.desc())).mappings().all()
    seen = set()
    for row in rows:
        key = tuple(row[column] for column in columns)
        if key in seen:
            bind.execute(table.delete().where(table.c.id == row["id"]))
        else:
            seen.add(key)


def _named_unique_constraints(table: str) -> set[str]:
    """Return only constraints this migration can safely identify and own."""
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_unique_constraints(table)
        if item.get("name")
    }


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("extracted_job_titles"):
        op.create_table(
            "extracted_job_titles",
            sa.Column("document_id", sa.String(length=80), nullable=False),
            sa.Column("text", sa.String(length=300), nullable=True),
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["jd_documents.document_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("document_id"),
        )

    metadata = sa.MetaData()
    records = sa.Table("jd_extraction_records", metadata, autoload_with=bind)
    titles = sa.Table("extracted_job_titles", metadata, autoload_with=bind)
    tasks = sa.Table("extracted_task_requirements", metadata, autoload_with=bind)
    requirements = sa.Table("extracted_candidate_requirements", metadata, autoload_with=bind)
    companies = sa.Table("extracted_company_facts", metadata, autoload_with=bind)
    employment = sa.Table("extracted_employment_facts", metadata, autoload_with=bind)
    evidence = sa.Table("extraction_evidence", metadata, autoload_with=bind)

    specifications = (
        ("responsibilities", tasks, "requirement_id", "task"),
        ("requirements", requirements, "requirement_id", "requirement"),
        ("company_facts", companies, "fact_id", "company_fact"),
        ("employment_facts", employment, "fact_id", "employment_fact"),
    )
    for row in _latest_payloads(bind, records):
        document_id = row["document_id"]
        payload = _json(row["payload"]) or {}
        job_title = payload.get("job_title")
        _insert_missing(bind, titles, {"document_id": document_id}, {
            "text": job_title.get("text") if job_title else None
        })
        if job_title:
            _canonical_evidence(
                bind, evidence, document_id, "job_title", "job_title", job_title
            )
        for collection, table, identifier, owner_type in specifications:
            for item in payload.get(collection, []):
                key = {"document_id": document_id, identifier: item[identifier]}
                values = {"payload": _without_evidence(item)}
                if table is requirements:
                    values.update(kind=item["kind"], modality=item.get("modality", "unknown"))
                _insert_missing(bind, table, key, values)
                _canonical_evidence(
                    bind, evidence, document_id, owner_type, item[identifier], item
                )

        for _collection, table, identifier, owner_type in specifications:
            structured = bind.execute(sa.select(table).where(
                table.c.document_id == document_id
            )).mappings().all()
            for item in structured:
                value = _json(item["payload"]) or {}
                _canonical_evidence(
                    bind, evidence, document_id, owner_type, item[identifier], value
                )
                if "evidence" in value:
                    bind.execute(table.update().where(table.c.id == item["id"]).values(
                        payload=_without_evidence(value)
                    ))

    for table, columns, name in (
        (tasks, ("document_id", "requirement_id"), "uq_extracted_task_document_requirement"),
        (companies, ("document_id", "fact_id"), "uq_extracted_company_document_fact"),
        (employment, ("document_id", "fact_id"), "uq_extracted_employment_document_fact"),
        (evidence, ("document_id", "owner_type", "owner_ref"), "uq_extraction_evidence_owner"),
    ):
        _deduplicate(bind, table, columns)
        existing = {
            tuple(item["column_names"])
            for item in sa.inspect(bind).get_unique_constraints(table.name)
        }
        if tuple(columns) not in existing:
            with op.batch_alter_table(table.name) as batch:
                batch.create_unique_constraint(name, list(columns))

    dialect = bind.dialect.name
    if dialect == "sqlite":
        op.execute("""CREATE TRIGGER trg_extraction_payload_reject_update
        BEFORE UPDATE OF payload ON jd_extraction_records
        WHEN NEW.payload IS NOT OLD.payload BEGIN
        SELECT RAISE(ABORT, 'extraction audit payload is immutable'); END""")
    elif dialect == "postgresql":
        op.execute("""CREATE FUNCTION reject_extraction_payload_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.payload IS DISTINCT FROM OLD.payload THEN
        RAISE EXCEPTION 'extraction audit payload is immutable'
        USING ERRCODE = 'integrity_constraint_violation'; END IF;
        RETURN NEW; END; $$""")
        op.execute("""CREATE TRIGGER trg_extraction_payload_reject_update
        BEFORE UPDATE ON jd_extraction_records FOR EACH ROW
        EXECUTE FUNCTION reject_extraction_payload_mutation()""")
    else:
        raise RuntimeError(f"unsupported database dialect {dialect!r}")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_extraction_payload_reject_update "
            "ON jd_extraction_records"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_extraction_payload_mutation()")
    else:
        op.execute("DROP TRIGGER IF EXISTS trg_extraction_payload_reject_update")
    for table, name in (
        ("extraction_evidence", "uq_extraction_evidence_owner"),
        ("extracted_employment_facts", "uq_extracted_employment_document_fact"),
        ("extracted_company_facts", "uq_extracted_company_document_fact"),
        ("extracted_task_requirements", "uq_extracted_task_document_requirement"),
    ):
        # 0001 created equivalent anonymous unique constraints on clean databases.
        # 0006 only creates the named constraint for legacy schemas where the
        # column set is absent, so downgrade must reverse only a constraint that
        # 0006 actually owns instead of assuming the name exists.
        if name in _named_unique_constraints(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(name, type_="unique")
    op.drop_table("extracted_job_titles")
