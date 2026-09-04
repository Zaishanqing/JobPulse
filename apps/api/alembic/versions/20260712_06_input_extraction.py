"""track truthful file input extraction

Revision ID: 20260712_06
Revises: 20260712_05
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_06"
down_revision: Union[str, Sequence[str], None] = "20260712_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_definition(column_name: str) -> sa.Column:
    definitions = {
        "file_id": sa.Column("file_id", sa.String(length=36), nullable=True),
        "input_extraction_status": sa.Column(
            "input_extraction_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_required",
        ),
        "input_provider": sa.Column("input_provider", sa.String(length=64), nullable=True),
        "input_error_code": sa.Column(
            "input_error_code", sa.String(length=128), nullable=True
        ),
        "input_error_message": sa.Column("input_error_message", sa.Text(), nullable=True),
    }
    return definitions[column_name]


def _add_missing_columns(table_name: str, target_columns: tuple[str, ...]) -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    missing = [column for column in target_columns if column not in existing]
    if not missing:
        return
    with op.batch_alter_table(table_name) as batch_op:
        for column_name in missing:
            batch_op.add_column(_column_definition(column_name))


def upgrade() -> None:
    extraction_columns = (
        "input_extraction_status",
        "input_provider",
        "input_error_code",
        "input_error_message",
    )
    _add_missing_columns("resumes", extraction_columns)
    _add_missing_columns("job_descriptions", ("file_id", *extraction_columns))

    inspector = sa.inspect(op.get_bind())
    file_foreign_key_exists = any(
        foreign_key.get("constrained_columns") == ["file_id"]
        for foreign_key in inspector.get_foreign_keys("job_descriptions")
    )
    if not file_foreign_key_exists:
        with op.batch_alter_table("job_descriptions") as batch_op:
            batch_op.create_foreign_key(
                "fk_job_descriptions_file_id_file_assets",
                "file_assets",
                ["file_id"],
                ["id"],
            )


def _drop_existing_columns(table_name: str, target_columns: tuple[str, ...]) -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    removable = [column for column in target_columns if column in existing]
    if not removable:
        return
    with op.batch_alter_table(table_name) as batch_op:
        for column_name in reversed(removable):
            batch_op.drop_column(column_name)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    file_foreign_key = next(
        (
            foreign_key
            for foreign_key in inspector.get_foreign_keys("job_descriptions")
            if foreign_key.get("constrained_columns") == ["file_id"]
        ),
        None,
    )
    if file_foreign_key and file_foreign_key.get("name"):
        with op.batch_alter_table("job_descriptions") as batch_op:
            batch_op.drop_constraint(file_foreign_key["name"], type_="foreignkey")
    extraction_columns = (
        "input_extraction_status",
        "input_provider",
        "input_error_code",
        "input_error_message",
    )
    _drop_existing_columns("job_descriptions", ("file_id", *extraction_columns))
    _drop_existing_columns("resumes", extraction_columns)
