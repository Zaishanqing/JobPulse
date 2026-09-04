from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import create_engine, text

from app.contexts.evidence_rag import EvidenceRagError, ManageEvidenceRag
from app.domain.accounts import AccountActor


LOGGER = logging.getLogger(__name__)
_INDEX_PROGRESS: dict[int, dict[str, object]] = {}
_BACKFILL_STARTED = False


def _progress_name(graph_version_id: int) -> str:
    return f"rag_index_progress:{graph_version_id}"


def _save_progress(main_engine, graph_version_id: int, progress: dict) -> None:
    payload = {**progress, "graph_version_id": graph_version_id}
    with main_engine.connect() as connection:
        connection.execute(
            text(
                """
                INSERT INTO system_configs (name, config, version, updated_at)
                VALUES (:name, CAST(:config AS json), 1, now())
                ON CONFLICT (name) DO UPDATE
                SET config = excluded.config,
                    version = system_configs.version + 1,
                    updated_at = now()
                """
            ),
            {
                "name": _progress_name(graph_version_id),
                "config": json.dumps(payload),
            },
        )
        connection.commit()


def _load_progress(main_engine, graph_version_id: int) -> dict | None:
    with main_engine.connect() as connection:
        row = connection.execute(
            text("SELECT config FROM system_configs WHERE name = :name"),
            {"name": _progress_name(graph_version_id)},
        ).mappings().first()
    value = row["config"] if row is not None else None
    return value if isinstance(value, dict) else None


def _clear_progress(main_engine, graph_version_id: int) -> None:
    with main_engine.connect() as connection:
        connection.execute(
            text("DELETE FROM system_configs WHERE name = :name"),
            {"name": _progress_name(graph_version_id)},
        )
        connection.commit()

PLATFORM_TENANT_REF = "jobgraph-platform-public"
PLATFORM_PERMISSION_SCOPE = "platform:public"
INTERNAL_INDEX_STATUS_ROLES = frozenset({"admin", "developer"})
PROTECTED_CV_INDEX_TYPES = frozenset({
    "cv_profile",
    "resume",
    "validated_cv_snapshot",
})
PROTECTED_ENTERPRISE_INDEX_TYPES = frozenset({
    "enterprise_job",
    "enterprise_position",
    "enterprise_job_profile",
})


@dataclass(frozen=True)
class PublishedGraphVersionIndexer:
    kg_database_url: str | None
    main_database_url: str | None
    rag: ManageEvidenceRag

    def index_graph_version(self, graph_version_id: int) -> dict[str, object]:
        if not self.kg_database_url or not self.main_database_url:
            LOGGER.warning(
                "rag_auto_index_skipped",
                extra={
                    "graph_version_id": graph_version_id,
                    "reason": "knowledge graph database url missing",
                },
            )
            return {"indexed_count": 0, "skipped": "database_url_missing"}
        if not self.rag.enabled or self.rag.store is None:
            LOGGER.warning(
                "rag_auto_index_skipped",
                extra={
                    "graph_version_id": graph_version_id,
                    "reason": "evidence rag disabled",
                },
            )
            return {"indexed_count": 0, "skipped": "rag_disabled"}
        kg_engine = create_engine(self.kg_database_url)
        main_engine = create_engine(self.main_database_url)
        try:
            items = self._records_for_graph_version(
                kg_engine, main_engine, graph_version_id
            )
        finally:
            kg_engine.dispose()
            main_engine.dispose()
        if not items:
            LOGGER.info(
                "rag_auto_index_nothing_to_index",
                extra={"graph_version_id": graph_version_id},
            )
            return {"indexed_count": 0, "graph_version_id": graph_version_id}
        actor = AccountActor(
            account_id="system:rag-auto-index",
            role="admin",
        )
        expected_total = len(items)
        progress_engine = create_engine(self.main_database_url)
        prior_progress = _load_progress(progress_engine, graph_version_id)
        resume_from = 0
        if (
            prior_progress is not None
            and int(prior_progress.get("expected", 0)) == expected_total
        ):
            candidate = int(prior_progress.get("processed", 0))
            if 0 < candidate < expected_total:
                resume_from = candidate
        progress = {
            "processed": resume_from,
            "expected": expected_total,
            "business_object_id": items[0]["business_object_id"],
            "graph_version_id": graph_version_id,
        }
        _INDEX_PROGRESS[graph_version_id] = progress
        try:
            _save_progress(progress_engine, graph_version_id, progress)
            indexed_count = 0
            remaining = items[resume_from:] if resume_from else items
            for start in range(0, len(remaining), 200):
                chunk = remaining[start : start + 200]
                result = None
                for attempt in range(3):
                    try:
                        result = self.rag.index(actor, tuple(chunk))
                        break
                    except EvidenceRagError as exc:
                        if attempt == 2:
                            LOGGER.error(
                                "rag_auto_index_failed",
                                extra={
                                    "graph_version_id": graph_version_id,
                                    "error_code": exc.code,
                                    "error_message": str(exc),
                                },
                            )
                            raise
                        LOGGER.warning(
                            "rag_auto_index_retry",
                            extra={
                                "graph_version_id": graph_version_id,
                                "attempt": attempt + 1,
                                "error_code": exc.code,
                            },
                        )
                        time.sleep(2 * (attempt + 1))
                assert result is not None
                indexed_count += int(result["indexed_count"])
                progress["processed"] = resume_from + indexed_count
                _save_progress(progress_engine, graph_version_id, progress)
        finally:
            _INDEX_PROGRESS.pop(graph_version_id, None)
            _clear_progress(progress_engine, graph_version_id)
            progress_engine.dispose()
        LOGGER.info(
            "rag_auto_index_completed",
            extra={
                "graph_version_id": graph_version_id,
                "indexed_count": indexed_count,
            },
        )
        return {
            "indexed_count": indexed_count,
            "business_object_id": items[0]["business_object_id"],
            "graph_version_id": graph_version_id,
        }

    def _records_for_graph_version(
        self,
        kg_engine,
        main_engine,
        graph_version_id: int,
    ) -> list[dict[str, object]]:
        with kg_engine.connect() as connection:
            version = connection.execute(
                text(
                    """
                    SELECT gv.id AS graph_version_id,
                           gv.position_id,
                           gv.version_name,
                           gv.build_run_id
                    FROM graph_versions gv
                    WHERE gv.id = :graph_version_id
                      AND gv.published_at IS NOT NULL
                    LIMIT 1
                    """
                ),
                {"graph_version_id": graph_version_id},
            ).mappings().first()
            if version is None:
                return []
            rows = connection.execute(
                text(
                    """
                    SELECT gbs.document_id,
                           d.source_version,
                           d.source_fact_version,
                           e.id AS evidence_id,
                           e.quote,
                           e.start,
                           e."end" AS location_end,
                           e.alignment,
                           e.occurrence_index
                    FROM graph_build_samples gbs
                    JOIN jd_documents d ON d.document_id = gbs.document_id
                    JOIN extraction_evidence e ON e.document_id = d.document_id
                    WHERE gbs.build_run_id = :build_run_id AND gbs.included
                    ORDER BY gbs.document_id, e.id
                    """
                ),
                {"build_run_id": version["build_run_id"]},
            ).mappings().all()
        business_object_id = self._main_position_id(
            main_engine, str(version["position_id"])
        )
        if business_object_id is None:
            return []
        business_object_name = self._position_name(
            main_engine, business_object_id
        )
        items: list[dict[str, object]] = []
        for row in rows:
            quote = str(row["quote"] or "").strip()
            if not quote:
                continue
            items.append(
                {
                    "evidence_id": (
                        f"real-jd:{row['document_id']}:{row['evidence_id']}"
                    ),
                    "business_object_type": "standard_position",
                    "business_object_id": business_object_id,
                    "business_object_name": business_object_name,
                    "evidence_type": "jd_evidence",
                    "source_object_type": "source_jd",
                    "source_object_id": row["document_id"],
                    "source_document_id": row["document_id"],
                    "graph_version_id": version["graph_version_id"],
                    "source_version": (
                        row["source_version"]
                        or row["source_fact_version"]
                        or row["document_id"]
                    ),
                    "text": quote,
                    "quote": quote,
                    "location_start": (
                        int(row["start"]) if row["start"] is not None else 0
                    ),
                    "location_end": (
                        int(row["location_end"])
                        if row["location_end"] is not None
                        else len(quote)
                    ),
                    "occurrence_index": int(row["occurrence_index"] or 0),
                    "alignment": str(row["alignment"] or "unresolved"),
                    "graph_version": version["version_name"],
                    "tenant_ref": PLATFORM_TENANT_REF,
                    "permission_scope": PLATFORM_PERMISSION_SCOPE,
                }
            )
        return items

    def _position_name(
        self, main_engine, main_system_id: str
    ) -> str | None:
        with main_engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT position_name
                    FROM standard_positions
                    WHERE id = :main_system_id
                    LIMIT 1
                    """
                ),
                {"main_system_id": main_system_id},
            ).mappings().first()
        return str(row["position_name"]) if row is not None else None

    def _main_position_id(
        self, main_engine, kg_position_id: str
    ) -> str | None:
        with main_engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT main_system_id
                    FROM knowledge_graph_entity_mappings
                    WHERE entity_type = 'position'
                      AND knowledge_graph_id = :kg_position_id
                      AND sync_status IN ('synced', 'confirmed')
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {"kg_position_id": kg_position_id},
            ).mappings().first()
        if row is not None:
            return str(row["main_system_id"])
        LOGGER.warning(
            "rag_auto_index_position_mapping_missing",
            extra={"kg_position_id": kg_position_id},
        )
        return None

    def index_incomplete_published(self) -> dict[str, object]:
        if not self.kg_database_url or not self.main_database_url:
            return {"indexed": 0, "skipped": "database_url_missing"}
        if not self.rag.enabled or self.rag.store is None:
            return {"indexed": 0, "skipped": "rag_disabled"}
        kg_engine = create_engine(self.kg_database_url)
        main_engine = create_engine(self.main_database_url)
        try:
            with main_engine.connect() as connection:
                acquired = connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": 727301},
                ).scalar_one()
            if not acquired:
                LOGGER.info("rag_auto_index_backfill_lock_busy")
                return {"indexed": 0, "skipped": "backfill_lock_busy"}
            with kg_engine.connect() as connection:
                versions = connection.execute(
                    text(
                        """
                        SELECT id, build_run_id, position_id
                        FROM graph_versions
                        WHERE published_at IS NOT NULL
                        ORDER BY id
                        """
                    )
                ).mappings().all()
            indexed = 0
            for version in versions:
                graph_version_id = int(version["id"])
                if graph_version_id in _INDEX_PROGRESS:
                    continue
                business_object_id = self._main_position_id(
                    main_engine, str(version["position_id"])
                )
                if business_object_id is None:
                    continue
                expected = self._expected_count_for_graph_version(
                    kg_engine, graph_version_id
                )
                if expected is None or expected == 0:
                    continue
                try:
                    current = self.rag.store.count(
                        business_object_type="standard_position",
                        business_object_id=business_object_id,
                        graph_version_id=graph_version_id,
                    )
                except Exception:
                    LOGGER.exception("rag_index_backfill_count_failed")
                    current = 0
                if current >= expected:
                    continue
                LOGGER.info(
                    "rag_auto_index_backfill_start",
                    extra={
                        "graph_version_id": graph_version_id,
                        "indexed": current,
                        "expected": expected,
                    },
                )
                result = self.index_graph_version(graph_version_id)
                indexed += int(result.get("indexed_count", 0))
            return {"indexed": indexed}
        finally:
            with main_engine.connect() as connection:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": 727301},
                )
            kg_engine.dispose()
            main_engine.dispose()

    @staticmethod
    def _expected_count_for_graph_version(
        kg_engine, graph_version_id: int
    ) -> int | None:
        with kg_engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT COUNT(*) AS expected
                    FROM graph_build_samples gbs
                    JOIN jd_documents d ON d.document_id = gbs.document_id
                    JOIN extraction_evidence e ON e.document_id = d.document_id
                    JOIN graph_versions gv ON gv.build_run_id = gbs.build_run_id
                    WHERE gv.id = :graph_version_id
                      AND gv.published_at IS NOT NULL
                      AND gbs.included
                      AND btrim(e.quote) <> ''
                    """
                ),
                {"graph_version_id": graph_version_id},
            ).mappings().first()
        return int(row["expected"]) if row is not None else None


def start_index_backfill(indexer: PublishedGraphVersionIndexer) -> None:
    global _BACKFILL_STARTED
    if _BACKFILL_STARTED:
        return
    _BACKFILL_STARTED = True

    def _run() -> None:
        try:
            indexer.index_incomplete_published()
        except Exception:
            LOGGER.exception("rag_auto_index_startup_backfill_failed")

    threading.Thread(
        target=_run,
        name="rag-index-backfill",
        daemon=True,
    ).start()


@dataclass(frozen=True)
class RagIndexStatusService:
    kg_database_url: str | None
    main_database_url: str | None
    rag: ManageEvidenceRag

    def status(
        self,
        *,
        actor: AccountActor,
        business_object_type: str,
        business_object_id: str,
        graph_version_id: int | None = None,
        graph_version: str | None = None,
        business_version: str | None = None,
    ) -> dict[str, object]:
        self._authorize_status(
            actor=actor,
            business_object_type=business_object_type,
            business_object_id=business_object_id,
        )
        if not self.rag.enabled or self.rag.store is None:
            return {
                "status": "disabled",
                "indexed_count": None,
                "expected_count": None,
            }
        try:
            indexed_count = self.rag.store.count(
                business_object_type=business_object_type,
                business_object_id=business_object_id,
                graph_version_id=graph_version_id,
                graph_version=graph_version,
                business_version=business_version,
            )
        except Exception:
            LOGGER.exception("rag_index_status_count_failed")
            return {
                "status": "unknown",
                "indexed_count": None,
                "expected_count": None,
            }
        if graph_version_id is not None and self.main_database_url:
            progress_engine = create_engine(self.main_database_url)
            try:
                db_progress = _load_progress(progress_engine, graph_version_id)
            finally:
                progress_engine.dispose()
            if db_progress is not None:
                processed = int(db_progress.get("processed", 0))
                expected = int(db_progress.get("expected", 0))
                if processed < expected:
                    return {
                        "status": "running",
                        "indexed_count": processed,
                        "expected_count": expected,
                    }
        running_progress = next(
            (
                item
                for item in _INDEX_PROGRESS.values()
                if item.get("business_object_id") == business_object_id
                and item.get("graph_version_id") == graph_version_id
            ),
            None,
        )
        if running_progress is not None:
            return {
                "status": "running",
                "indexed_count": int(running_progress["processed"]),
                "expected_count": int(running_progress["expected"]),
            }
        expected_count = self._expected_count(
            business_object_type=business_object_type,
            business_object_id=business_object_id,
            graph_version_id=graph_version_id,
        )
        if expected_count is None:
            return {
                "status": "unknown",
                "indexed_count": indexed_count,
                "expected_count": None,
            }
        status = (
            "completed"
            if expected_count == 0 or indexed_count >= expected_count
            else "running"
        )
        return {
            "status": status,
            "indexed_count": indexed_count,
            "expected_count": expected_count,
        }

    def _authorize_status(
        self,
        *,
        actor: AccountActor,
        business_object_type: str,
        business_object_id: str,
    ) -> None:
        if actor.role in INTERNAL_INDEX_STATUS_ROLES:
            return
        if business_object_type == "standard_position":
            # Published standard positions are platform catalogue resources.
            return
        if business_object_type in PROTECTED_CV_INDEX_TYPES:
            if self._cv_index_owned_by(business_object_id, actor.account_id):
                return
            raise EvidenceRagError(
                "RAG_INDEX_STATUS_PERMISSION_DENIED",
                "CV index status is outside the current user's ownership",
            )
        if business_object_type in PROTECTED_ENTERPRISE_INDEX_TYPES:
            if actor.role == "enterprise_user" and self._enterprise_index_owned_by(
                business_object_id, actor.account_id
            ):
                return
            raise EvidenceRagError(
                "RAG_INDEX_STATUS_PERMISSION_DENIED",
                "Enterprise position index status is outside the current enterprise",
            )
        raise EvidenceRagError(
            "RAG_INDEX_STATUS_PERMISSION_DENIED",
            "Index status is not available for this business object",
        )

    def _cv_index_owned_by(self, business_object_id: str, actor_id: str) -> bool:
        if not self.main_database_url:
            return False
        profile_id = self._strip_object_prefix(
            business_object_id,
            ("cv-profile:", "cv_profile:", "resume:"),
        )
        engine = create_engine(self.main_database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        """
                        SELECT 1
                        FROM resumes r
                        LEFT JOIN validated_cv_snapshots snapshot
                          ON snapshot.id = r.validated_cv_snapshot_id
                        LEFT JOIN source_cv_versions source_version
                          ON source_version.id = COALESCE(
                              snapshot.source_cv_version_id,
                              r.source_cv_version_id
                          )
                        WHERE r.user_id = :actor_id
                          AND (
                              r.id = :business_object_id
                              OR r.id = :profile_id
                              OR r.validated_cv_snapshot_id = :profile_id
                              OR snapshot.id = :profile_id
                              OR source_version.id = :profile_id
                          )
                        LIMIT 1
                        """
                    ),
                    {
                        "actor_id": actor_id,
                        "business_object_id": business_object_id,
                        "profile_id": profile_id,
                    },
                ).first()
            return row is not None
        except Exception:
            LOGGER.exception("rag_index_status_cv_authorization_failed")
            return False
        finally:
            engine.dispose()

    def _enterprise_index_owned_by(
        self, business_object_id: str, actor_id: str
    ) -> bool:
        if not self.main_database_url:
            return False
        job_id = self._strip_object_prefix(
            business_object_id,
            ("enterprise-job:", "enterprise_job:", "enterprise-position:"),
        )
        engine = create_engine(self.main_database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        """
                        SELECT 1
                        FROM enterprise_jobs job
                        JOIN enterprises enterprise
                          ON enterprise.id = job.enterprise_id
                        WHERE job.id = :job_id
                          AND enterprise.owner_user_id = :actor_id
                        LIMIT 1
                        """
                    ),
                    {
                        "actor_id": actor_id,
                        "job_id": job_id,
                    },
                ).first()
            return row is not None
        except Exception:
            LOGGER.exception("rag_index_status_enterprise_authorization_failed")
            return False
        finally:
            engine.dispose()

    @staticmethod
    def _strip_object_prefix(
        business_object_id: str, prefixes: tuple[str, ...]
    ) -> str:
        for prefix in prefixes:
            if business_object_id.startswith(prefix):
                return business_object_id[len(prefix) :]
        return business_object_id

    def status_for_payload(
        self, payload: Mapping[str, object]
    ) -> dict[str, object]:
        business_object = payload.get("business_object")
        if not isinstance(business_object, Mapping):
            return {
                "status": "unknown",
                "indexed_count": None,
                "expected_count": None,
            }
        graph_version_id = payload.get("graph_version_id")
        if graph_version_id is not None:
            try:
                graph_version_id = int(graph_version_id)
            except (TypeError, ValueError):
                graph_version_id = None
        return self.status(
            actor=AccountActor(
                account_id="system:rag-query",
                role="admin",
            ),
            business_object_type=str(business_object.get("object_type") or ""),
            business_object_id=str(business_object.get("object_id") or ""),
            graph_version_id=graph_version_id,
            graph_version=payload.get("graph_version"),
            business_version=payload.get("business_version"),
        )

    def _expected_count(
        self,
        *,
        business_object_type: str,
        business_object_id: str,
        graph_version_id: int | None,
    ) -> int | None:
        if (
            business_object_type != "standard_position"
            or graph_version_id is None
            or not self.kg_database_url
            or not self.main_database_url
        ):
            return None
        kg_engine = create_engine(self.kg_database_url)
        main_engine = create_engine(self.main_database_url)
        try:
            return self._expected_count_for_version(
                main_engine,
                kg_engine,
                business_object_id,
                graph_version_id,
            )
        except Exception:
            LOGGER.exception("rag_index_status_expected_count_failed")
            return None
        finally:
            kg_engine.dispose()
            main_engine.dispose()

    def _expected_count_for_version(
        self,
        main_engine,
        kg_engine,
        business_object_id: str,
        graph_version_id: int,
    ) -> int | None:
        with main_engine.connect() as connection:
            mapping = connection.execute(
                text(
                    """
                    SELECT knowledge_graph_id
                    FROM knowledge_graph_entity_mappings
                    WHERE entity_type = 'position'
                      AND main_system_id = :main_system_id
                      AND sync_status IN ('synced', 'confirmed')
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {"main_system_id": business_object_id},
            ).mappings().first()
        if mapping is None:
            return None
        with kg_engine.connect() as connection:
            version = connection.execute(
                text(
                    """
                    SELECT build_run_id
                    FROM graph_versions
                    WHERE id = :graph_version_id
                      AND position_id = :position_id
                    LIMIT 1
                    """
                ),
                {
                    "graph_version_id": graph_version_id,
                    "position_id": str(mapping["knowledge_graph_id"]),
                },
            ).mappings().first()
            if version is None:
                return None
            count = connection.execute(
                text(
                    """
                    SELECT COUNT(*) AS expected
                    FROM graph_build_samples gbs
                    JOIN jd_documents d ON d.document_id = gbs.document_id
                    JOIN extraction_evidence e ON e.document_id = d.document_id
                    WHERE gbs.build_run_id = :build_run_id
                      AND gbs.included
                      AND btrim(e.quote) <> ''
                    """
                ),
                {"build_run_id": version["build_run_id"]},
            ).mappings().first()
        return int(count["expected"]) if count is not None else None


@dataclass(frozen=True)
class RagPositionProfileService:
    kg_database_url: str | None
    main_database_url: str | None

    def profile_text(
        self, payload: Mapping[str, object]
    ) -> str | None:
        business_object = payload.get("business_object")
        if not isinstance(business_object, Mapping):
            return None
        if str(business_object.get("object_type") or "") != "standard_position":
            return None
        graph_version_id = payload.get("graph_version_id")
        if graph_version_id is None:
            return None
        try:
            graph_version_id = int(graph_version_id)
        except (TypeError, ValueError):
            return None
        if not self.kg_database_url or not self.main_database_url:
            return None
        business_object_id = str(business_object.get("object_id") or "")
        if not business_object_id:
            return None
        kg_engine = create_engine(self.kg_database_url)
        main_engine = create_engine(self.main_database_url)
        try:
            return self._profile_text_for_version(
                main_engine,
                kg_engine,
                business_object_id,
                graph_version_id,
            )
        except Exception:
            LOGGER.exception("rag_position_profile_failed")
            return None
        finally:
            kg_engine.dispose()
            main_engine.dispose()

    def _profile_text_for_version(
        self,
        main_engine,
        kg_engine,
        business_object_id: str,
        graph_version_id: int,
    ) -> str | None:
        with main_engine.connect() as connection:
            mapping = connection.execute(
                text(
                    """
                    SELECT knowledge_graph_id
                    FROM knowledge_graph_entity_mappings
                    WHERE entity_type = 'position'
                      AND main_system_id = :main_system_id
                      AND sync_status IN ('synced', 'confirmed')
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {"main_system_id": business_object_id},
            ).mappings().first()
        if mapping is None:
            return None
        with kg_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    """
                    SELECT snapshot
                    FROM graph_versions
                    WHERE id = :graph_version_id
                      AND position_id = :position_id
                    LIMIT 1
                    """
                ),
                {
                    "graph_version_id": graph_version_id,
                    "position_id": str(mapping["knowledge_graph_id"]),
                },
            ).mappings().first()
        if snapshot is None or not isinstance(snapshot["snapshot"], dict):
            return None
        return self._build_profile_text(snapshot["snapshot"])

    def _build_profile_text(self, snapshot: Mapping) -> str:
        parts: list[str] = []
        position = snapshot.get("position")
        if isinstance(position, Mapping) and position.get("name"):
            parts.append(f"岗位：{position['name']}")

        def collect(kind: str, limit: int) -> list[str]:
            seen: set[str] = set()
            values: list[str] = []
            for item in snapshot.get("requirement_profile") or []:
                if not isinstance(item, Mapping) or item.get("kind") != kind:
                    continue
                text = str(item.get("text") or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    values.append(text)
                if len(values) >= limit:
                    break
            return values

        education = collect("education", 6)
        if education:
            parts.append("学历要求：" + "；".join(education))
        experience = collect("experience", 6)
        if experience:
            parts.append("经验要求：" + "；".join(experience))
        certificate = collect("certificate", 6)
        if certificate:
            parts.append("证书要求：" + "；".join(certificate))
        soft = collect("soft_skill", 6)
        if soft:
            parts.append("软性要求：" + "；".join(soft))

        skills: list[str] = []
        seen_skills: set[str] = set()
        for item in snapshot.get("skill_relations") or []:
            if not isinstance(item, Mapping):
                continue
            name = str(
                item.get("canonical_name")
                or item.get("skill_name")
                or ""
            ).strip()
            if name and name not in seen_skills:
                seen_skills.add(name)
                skills.append(name)
            if len(skills) >= 20:
                break
        if skills:
            parts.append("核心技能：" + "、".join(skills))

        responsibilities: list[str] = []
        for item in snapshot.get("responsibilities") or []:
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                responsibilities.append(text)
            if len(responsibilities) >= 5:
                break
        if responsibilities:
            parts.append("岗位职责：" + "；".join(responsibilities))
        return "；".join(parts)


__all__ = [
    "PublishedGraphVersionIndexer",
    "RagIndexStatusService",
    "RagPositionProfileService",
    "start_index_backfill",
]
