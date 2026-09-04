"""Index validated fact hashes for constant-memory cross-source duplicate checks.

Revision ID: 20260801_46
Revises: 20260801_45
"""

from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa


revision = "20260801_46"
down_revision = "20260801_45"
branch_labels = None
depends_on = None

COLLECTIONS = {
    "responsibilities": "responsibility",
    "requirements": "requirement",
    "company_facts": "company_fact",
    "employment_facts": "employment_fact",
}


def _canonical_hash(fact_type: str, item: dict) -> str:
    value = {
        str(key): value
        for key, value in item.items()
        if key not in {"evidence", "requirement_id", "fact_id"}
    }
    material = {"fact_type": fact_type, "value": value}
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "validated_fact_hashes" not in inspector.get_table_names():
        op.create_table(
            "validated_fact_hashes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "snapshot_id",
                sa.String(36),
                sa.ForeignKey("validated_bundle_snapshots.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_jd_version_id",
                sa.String(36),
                sa.ForeignKey("source_jd_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("canonical_hash", sa.String(71), nullable=False),
            sa.UniqueConstraint(
                "snapshot_id",
                "canonical_hash",
                name="uq_validated_fact_hashes_snapshot_hash",
            ),
        )
        op.create_index(
            "ix_validated_fact_hashes_snapshot_id",
            "validated_fact_hashes",
            ["snapshot_id"],
        )
        op.create_index(
            "ix_validated_fact_hashes_source_jd_version_id",
            "validated_fact_hashes",
            ["source_jd_version_id"],
        )
        op.create_index(
            "ix_validated_fact_hashes_canonical_hash",
            "validated_fact_hashes",
            ["canonical_hash"],
        )
    else:
        actual_columns = {
            column["name"] for column in inspector.get_columns("validated_fact_hashes")
        }
        required_columns = {
            "id",
            "snapshot_id",
            "source_jd_version_id",
            "canonical_hash",
        }
        if not required_columns <= actual_columns:
            missing = sorted(required_columns - actual_columns)
            raise RuntimeError(
                "validated_fact_hashes has incompatible schema; missing columns: "
                + ", ".join(missing)
            )

    snapshots = connection.execute(
        sa.text(
            "SELECT id, source_jd_version_id, bundle_payload "
            "FROM validated_bundle_snapshots ORDER BY id"
        )
    ).mappings()
    batch: list[dict[str, str]] = []
    insert_statement = sa.text(
        "INSERT INTO validated_fact_hashes "
        "(id, snapshot_id, source_jd_version_id, canonical_hash) "
        "VALUES (:id, :snapshot_id, :source_jd_version_id, :canonical_hash)"
    )
    for snapshot in snapshots:
        payload = snapshot["bundle_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        extraction = payload.get("extraction_result", {})
        hashes = {
            _canonical_hash(fact_type, item)
            for collection, fact_type in COLLECTIONS.items()
            for item in extraction.get(collection, []) or []
        }
        for canonical_hash in sorted(hashes):
            batch.append(
                {
                    "id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"validated-fact-hash:{snapshot['id']}:{canonical_hash}",
                        )
                    ),
                    "snapshot_id": snapshot["id"],
                    "source_jd_version_id": snapshot["source_jd_version_id"],
                    "canonical_hash": canonical_hash,
                }
            )
        if len(batch) >= 1000:
            connection.execute(insert_statement, batch)
            batch.clear()
    if batch:
        connection.execute(insert_statement, batch)


def downgrade() -> None:
    raise RuntimeError("validation fact hash indexing is an intentional migration")
