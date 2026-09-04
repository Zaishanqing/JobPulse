from __future__ import annotations

import argparse
import json
import re

from sqlalchemy import create_engine, inspect, text


def normalize_sql(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value.strip()).casefold()
    return re.sub(r"\s*([(),;=])\s*", r"\1", normalized)


def describe_schema(database_url: str) -> dict:
    engine = create_engine(database_url)
    inspector = inspect(engine)
    result = {}
    for table in sorted(set(inspector.get_table_names()) - {"alembic_version"}):
        columns = sorted(
            ({"name": item["name"], "type": str(item["type"]),
              "nullable": item["nullable"], "default": str(item.get("default"))}
             for item in inspector.get_columns(table)),
            key=lambda item: item["name"],
        )
        foreign_keys = sorted({(tuple(item["constrained_columns"]),
                                item["referred_table"], tuple(item["referred_columns"]))
                               for item in inspector.get_foreign_keys(table)})
        unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table)}
        indexes = {(tuple(item["column_names"]), bool(item.get("unique")))
                   for item in inspector.get_indexes(table)}
        primary_key = inspector.get_pk_constraint(table).get("constrained_columns") or []
        checks = sorted({
            # SQLite assigns unstable names when batch migrations rebuild tables;
            # the check expression is the schema contract, not its generated name.
            (None, normalize_sql(item.get("sqltext")))
            for item in inspector.get_check_constraints(table)
        }, key=lambda item: (item[0] or "", item[1] or ""))
        result[table] = {"columns": columns,
                         "primary_key": primary_key,
                         "checks": [[name, sql] for name, sql in checks],
                         "foreign_keys": [[list(a), b, list(c)] for a, b, c in foreign_keys],
                         "unique": [list(value) for value in sorted(unique)],
                         "indexes": [[list(cols), flag] for cols, flag in sorted(indexes)]}
    views = {
        name: normalize_sql(inspector.get_view_definition(name))
        for name in sorted(inspector.get_view_names())
    }
    triggers = []
    sqlite_objects = []
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            objects = connection.execute(text(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND name != 'alembic_version' "
                "AND type IN ('index', 'trigger', 'view') "
                "ORDER BY type, name"
            )).mappings().all()
            sqlite_objects = [{
                "type": row["type"], "name": row["name"],
                "table": row["tbl_name"], "sql": normalize_sql(row["sql"]),
            } for row in objects]
            triggers = [item for item in sqlite_objects if item["type"] == "trigger"]
    result["$views"] = views
    result["$triggers"] = triggers
    result["$sqlite_objects"] = sqlite_objects
    engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database_urls", nargs="+")
    args = parser.parse_args()
    schemas = []
    for url in args.database_urls:
        schema = describe_schema(url)
        schemas.append(schema)
        print(json.dumps({"database_url": url,
                          "tables": len([key for key in schema if not key.startswith('$')]),
                          "triggers": [item["name"] for item in schema["$triggers"]]}))
    if any(schema != schemas[0] for schema in schemas[1:]):
        raise SystemExit("database schemas differ")


if __name__ == "__main__":
    main()
