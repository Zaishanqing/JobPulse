from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Engine, inspect, text

from app.config import Settings


EXPECTED_MIGRATION_REVISION = "0026_review_decision_effects"


class DatabaseReadiness:
    def __init__(self, engine: Engine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    def check(self) -> dict:
        dialect = self.engine.dialect.name
        try:
            with self.engine.begin() as connection:
                schema = inspect(connection)
                revision = (
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one_or_none()
                    if schema.has_table("alembic_version")
                    else None
                )
                probe_name = f"kg_readiness_probe_{uuid4().hex}"
                quoted_probe_name = connection.dialect.identifier_preparer.quote(
                    probe_name
                )
                connection.execute(
                    text(
                        f"CREATE TEMPORARY TABLE {quoted_probe_name} "
                        "(id INTEGER NOT NULL)"
                    )
                )
                connection.execute(
                    text(f"INSERT INTO {quoted_probe_name} (id) VALUES (1)")
                )
                writable = connection.execute(
                    text(f"SELECT COUNT(*) FROM {quoted_probe_name}")
                ).scalar_one() == 1
                connection.execute(text(f"DROP TABLE {quoted_probe_name}"))
                catalog_versions = (
                    list(
                        connection.execute(
                            text(
                                "SELECT DISTINCT taxonomy_version FROM skills "
                                "WHERE status = 'active' AND taxonomy_version IS NOT NULL "
                                "ORDER BY taxonomy_version"
                            )
                        ).scalars()
                    )
                    if schema.has_table("skills")
                    else []
                )
                if schema.has_table("standard_positions"):
                    position_stats = connection.execute(
                        text(
                            "SELECT COUNT(*) AS total_positions, "
                            "COUNT(position_code) AS mapped_positions, "
                            "COUNT(DISTINCT position_code) AS unique_positions, "
                            "COUNT(*) FILTER (WHERE status = 'active') AS active_positions, "
                            "COUNT(*) FILTER (WHERE status = 'active' "
                            "AND sample_support_status = 'sufficient' "
                            "AND current_version_id IS NOT NULL) AS published_profiles "
                            "FROM standard_positions"
                        )
                    ).mappings().one()
                    position_catalog_versions = list(
                        connection.execute(
                            text(
                                "SELECT DISTINCT taxonomy_version FROM standard_positions "
                                "WHERE taxonomy_version IS NOT NULL ORDER BY taxonomy_version"
                            )
                        ).scalars()
                    )
                else:
                    position_stats = {
                        "total_positions": 0,
                        "mapped_positions": 0,
                        "unique_positions": 0,
                        "active_positions": 0,
                        "published_profiles": 0,
                    }
                    position_catalog_versions = []
        except Exception as exc:
            return {
                "status": "not_ready",
                "database": "unavailable",
                "database_engine": dialect,
                "error": type(exc).__name__,
            }
        production = self.settings.environment.casefold() == "production"
        expected_positions = self.settings.expected_position_catalog_count
        position_catalog_complete = (
            position_stats["total_positions"] == expected_positions
            and position_stats["mapped_positions"] == expected_positions
            and position_stats["unique_positions"] == expected_positions
            and position_catalog_versions == ["position-taxonomy.v3.0.0"]
        )
        checks = {
            "postgresql": dialect == "postgresql" if production else True,
            "migration_head": revision == EXPECTED_MIGRATION_REVISION,
            "write_probe": writable,
            "catalog_version": bool(catalog_versions),
            "position_catalog_complete": position_catalog_complete,
            "published_position_profile": position_stats["published_profiles"] > 0,
        }
        required = (
            dict(checks)
            if production
            else {"write_probe": checks["write_probe"]}
        )
        if self.settings.position_catalog_readiness_required:
            required.update(
                {
                    "migration_head": checks["migration_head"],
                    "position_catalog_complete": checks["position_catalog_complete"],
                    "published_position_profile": checks["published_position_profile"],
                }
            )
        return {
            "status": "ready" if all(required.values()) else "not_ready",
            "database": "ok",
            "database_engine": dialect,
            "transaction_isolation": "READ COMMITTED" if dialect == "postgresql" else None,
            "migration_revision": revision,
            "expected_migration_revision": EXPECTED_MIGRATION_REVISION,
            "catalog_versions": catalog_versions,
            "position_catalog_version": (
                position_catalog_versions[0]
                if len(position_catalog_versions) == 1
                else None
            ),
            "position_catalog": {
                "expected_positions": expected_positions,
                "total_positions": position_stats["total_positions"],
                "mapped_positions": position_stats["mapped_positions"],
                "unique_positions": position_stats["unique_positions"],
                "active_positions": position_stats["active_positions"],
                "published_profiles": position_stats["published_profiles"],
            },
            "checks": checks,
        }
