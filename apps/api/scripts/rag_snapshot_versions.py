"""Graph version mapping helpers for RAG snapshot benchmarks."""

from __future__ import annotations

import sqlite3


def graph_version_by_document(
    cursor: sqlite3.Cursor,
) -> dict[str, int | None]:
    """Map each document to its latest graph version through the real FK chain.

    ``position_skill_supports`` links a document/evidence to a position;
    ``graph_versions`` holds the latest published version per position.
    Looking the document id up directly in the position->version map is a
    key mismatch, so the chain is resolved explicitly here.
    """

    version_by_position: dict[str, int] = {}
    try:
        cursor.execute(
            """
            SELECT position_id, MAX(id)
            FROM graph_versions
            GROUP BY position_id
            """
        )
        for position_id, version_id in cursor.fetchall():
            version_by_position.setdefault(str(position_id), int(version_id))
    except sqlite3.OperationalError:
        version_by_position = {}
    position_by_document: dict[str, str] = {}
    try:
        cursor.execute(
            """
            SELECT DISTINCT position_id, document_id
            FROM position_skill_supports
            """
        )
        for position_id, document_id in cursor.fetchall():
            position_by_document.setdefault(str(document_id), str(position_id))
    except sqlite3.OperationalError:
        position_by_document = {}
    return {
        document_id: version_by_position.get(position_id)
        for document_id, position_id in position_by_document.items()
    }
