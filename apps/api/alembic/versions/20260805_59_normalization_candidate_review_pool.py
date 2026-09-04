"""extend normalization candidates into a review pool

Revision ID: 20260805_59
Revises: 20260804_58
"""

from datetime import datetime, timezone
import unicodedata

import sqlalchemy as sa
from alembic import op


revision = "20260805_59"
down_revision = "20260804_58"
branch_labels = None
depends_on = None


def _clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _normalize(value: str) -> str:
    return _clean(value).casefold()


def _new_status(old_status: str, skill_id: str | None) -> str:
    if old_status == "confirmed":
        return "mapped_existing" if skill_id else "created_new"
    if old_status == "rejected":
        return "excluded_non_skill"
    return "pending"


def upgrade() -> None:
    existing_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "skill_normalization_candidates"
        )
    }
    if "normalized_skill" in existing_columns:
        return
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("normalized_skill", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("occurrence_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("source_type", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("evidence_samples", sa.JSON(), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("reviewer_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "skill_normalization_candidates",
        sa.Column("decision_reason", sa.Text(), nullable=True),
    )

    table = sa.table(
        "skill_normalization_candidates",
        sa.column("id", sa.String()),
        sa.column("raw_skill", sa.String()),
        sa.column("candidate_skill_id", sa.String()),
        sa.column("context", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("normalized_skill", sa.String()),
        sa.column("occurrence_count", sa.Integer()),
        sa.column("source_type", sa.String()),
        sa.column("evidence_samples", sa.JSON()),
        sa.column("first_seen_at", sa.DateTime(timezone=True)),
        sa.column("last_seen_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            table.c.id,
            table.c.raw_skill,
            table.c.candidate_skill_id,
            table.c.context,
            table.c.status,
            table.c.created_at,
            table.c.updated_at,
        ).order_by(table.c.created_at, table.c.id)
    ).mappings().all()

    grouped = {}
    for row in rows:
        grouped.setdefault(_normalize(row["raw_skill"]), []).append(row)

    now = datetime.now(timezone.utc)
    for normalized_skill, duplicates in grouped.items():
        keeper = duplicates[0]
        first_seen = keeper["created_at"] or now
        last_seen = max(
            (row["updated_at"] or row["created_at"] or now for row in duplicates),
        )
        evidence_samples = []
        for row in duplicates:
            if row["context"] and row["context"] not in {
                sample["evidence"] for sample in evidence_samples
            }:
                evidence_samples.append(
                    {
                        "source_type": "unknown",
                        "evidence": row["context"],
                        "observed_at": (row["created_at"] or now).isoformat(),
                    }
                )
        bind.execute(
            table.update()
            .where(table.c.id == keeper["id"])
            .values(
                raw_skill=_clean(keeper["raw_skill"]),
                normalized_skill=normalized_skill,
                occurrence_count=len(duplicates),
                source_type="unknown",
                evidence_samples=evidence_samples,
                status=_new_status(
                    keeper["status"], keeper["candidate_skill_id"]
                ),
                first_seen_at=first_seen,
                last_seen_at=last_seen,
            )
        )
        duplicate_ids = [row["id"] for row in duplicates[1:]]
        if duplicate_ids:
            bind.execute(table.delete().where(table.c.id.in_(duplicate_ids)))

    with op.batch_alter_table("skill_normalization_candidates") as batch_op:
        batch_op.drop_constraint(
            "ck_skill_normalization_candidates_status_allowed",
            type_="check",
        )
        batch_op.alter_column("normalized_skill", nullable=False)
        batch_op.alter_column("occurrence_count", nullable=False)
        batch_op.alter_column("source_type", nullable=False)
        batch_op.alter_column("evidence_samples", nullable=False)
        batch_op.alter_column("first_seen_at", nullable=False)
        batch_op.alter_column("last_seen_at", nullable=False)
        batch_op.create_check_constraint(
            "ck_skill_normalization_candidates_status_allowed",
            "status in ('pending', 'mapped_existing', 'created_new', "
            "'excluded_non_skill', 'deferred')",
        )
        batch_op.create_check_constraint(
            "ck_skill_normalization_candidates_source_type_allowed",
            "source_type in ('jd', 'cv', 'manual', 'unknown')",
        )
        batch_op.create_index(
            "ix_skill_normalization_candidates_normalized_skill",
            ["normalized_skill"],
            unique=False,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE skill_normalization_candidates SET status = CASE "
        "WHEN status IN ('mapped_existing', 'created_new') THEN 'confirmed' "
        "WHEN status = 'excluded_non_skill' THEN 'rejected' "
        "ELSE 'pending' END"
    )
    with op.batch_alter_table("skill_normalization_candidates") as batch_op:
        batch_op.drop_index(
            "ix_skill_normalization_candidates_normalized_skill"
        )
        batch_op.drop_constraint(
            "ck_skill_normalization_candidates_source_type_allowed",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_skill_normalization_candidates_status_allowed",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_skill_normalization_candidates_status_allowed",
            "status in ('pending', 'confirmed', 'rejected')",
        )
        for column in (
            "decision_reason",
            "reviewed_at",
            "reviewer_id",
            "last_seen_at",
            "first_seen_at",
            "evidence_samples",
            "source_type",
            "occurrence_count",
            "normalized_skill",
        ):
            batch_op.drop_column(column)
